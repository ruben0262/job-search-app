"""The claim protocol."""
from __future__ import annotations

import logging
import uuid as _uuid
from collections.abc import Sequence
from datetime import timedelta

from pymongo import ReturnDocument
from pymongo.database import Database
from pymongo.errors import BulkWriteError

from libs import settings
from libs.db import find, next_ids, now
from libs.db.models import Alert, LanePause, QueueJob, User
from libs.db.models.queue import CLAIM_TIMEOUT_MIN, LANES, MAX_ATTEMPTS
from libs.membership import having
from libs.notify.digest import due

logger = logging.getLogger(__name__)

# MongoDB's code for a write the unique index refused.
DUPLICATE_KEY = 11000


def lane_paused(db: Database, lane: str) -> bool:
    """Whether an operator has paused this lane (`deploy/scripts/pipeline_switch.py`)."""
    return db[LanePause.COLLECTION].count_documents({"_id": lane}, limit=1) > 0


def enqueue(db: Database, lane: str, user_id: _uuid.UUID, *,
            payload: dict | None = None, dedup_key: str | None = None,
            delay_min: int = 0) -> int | None:
    """Add work. `None` when the lane is paused or the same work (lane, user, dedup key) is pending."""
    added = enqueue_many(db, lane, [user_id], payload=payload, dedup_key=dedup_key, delay_min=delay_min)
    return added[0] if added else None


def enqueue_many(db: Database, lane: str, user_ids: Sequence[_uuid.UUID], *,
                 payload: dict | None = None, dedup_key: str | None = None,
                 delay_min: int = 0) -> list[int]:
    """The same work for each of these accounts, in one write: the ids of the jobs added. None while the lane
    is paused, and none for an account whose same work (lane, user, dedup key) is pending. One write, because
    the alerts queue hundreds at once (`libs.alerts`): one at a time, 428 of them took 1.2 s (2026-11-17)."""
    _check_lane(lane)
    if not user_ids:
        return []
    if lane_paused(db, lane):
        # Refused at the door rather than claimed and dropped.
        logger.info("queue: %s is paused, not enqueuing", lane)
        return []
    jobs = [QueueJob(id=job_id, lane=lane, user_id=user_id, dedup_key=dedup_key, payload=payload or {},
                     run_after=now() + timedelta(minutes=delay_min))
            for job_id, user_id in zip(next_ids(db, QueueJob.COLLECTION, len(user_ids)), user_ids,
                                       strict=True)]
    try:
        db[QueueJob.COLLECTION].insert_many([job.to_doc() for job in jobs], ordered=False)
    except BulkWriteError as exc:
        # The same work already pending is refused by the dedup index, and is not a fault; anything else is.
        errors = exc.details["writeErrors"]
        if any(error["code"] != DUPLICATE_KEY for error in errors):
            raise
        refused = {error["index"] for error in errors}
        return [job.id for index, job in enumerate(jobs) if index not in refused]
    return [job.id for job in jobs]


def claim(db: Database, lane: str, worker: str) -> QueueJob | None:
    """Take the next ready job in this lane, or `None`. One atomic write, so two workers never share one."""
    _check_lane(lane)
    return QueueJob.from_doc(db[QueueJob.COLLECTION].find_one_and_update(
        {"lane": lane, "state": "ready", "run_after": {"$lte": now()}},
        {"$set": {"state": "claimed", "claimed_at": now(), "claimed_by": worker}, "$inc": {"attempts": 1}},
        sort=[("run_after", 1), ("_id", 1)], return_document=ReturnDocument.AFTER))


def complete(db: Database, job: QueueJob) -> None:
    """Mark done."""
    _set(db, job, state="done", finished_at=now(), last_error=None)


def fail(db: Database, job: QueueJob, error: str, *,
         retry_in_min: int = 1, max_attempts: int = MAX_ATTEMPTS) -> str:
    """Record a failure: retried later, or dead once its attempts are spent."""
    if job.attempts >= max_attempts:
        _set(db, job, state="dead", finished_at=now(), last_error=error[:2000])
        return "dead"
    _set(db, job, state="ready", claimed_at=None, claimed_by=None, last_error=error[:2000],
         run_after=now() + timedelta(minutes=retry_in_min))
    return "ready"


def terminate(db: Database, job: QueueJob, reason: str) -> None:
    """Dead-letter immediately, without spending the remaining attempts."""
    _set(db, job, state="dead", finished_at=now(), last_error=reason[:2000])


def sweep_expired(db: Database, lane: str | None = None) -> int:
    """Return claims older than the lane's visibility timeout to `ready`."""
    lanes: Sequence[str] = (lane,) if lane else LANES
    recovered = 0
    for name in lanes:
        _check_lane(name)
        recovered += db[QueueJob.COLLECTION].update_many(
            {"lane": name, "state": "claimed",
             "claimed_at": {"$lt": now() - timedelta(minutes=CLAIM_TIMEOUT_MIN[name])}},
            {"$set": {"state": "ready", "claimed_at": None, "claimed_by": None}}).modified_count
    return recovered


def dispatch_due_digests(db: Database) -> int:
    """Enqueue a daily email for everyone whose is due (`libs.notify.digest.due`): an alert of theirs sends
    to it. On the same sweep as the alerts, so it has no clock of its own; the `digest` dedup key keeps it to
    one at a time."""
    hour = settings.get(db, "email_digest_hour")
    emailed = db[Alert.COLLECTION].distinct("user_id", {"on": True, "channels": "email"})
    enqueued = 0
    for user in find(db, User, {"$and": [{"_id": {"$in": emailed}, "status": "active"},
                                         having("alerts")]}):
        if due(db, user, hour) and enqueue(db, "notify", user.id, payload={"digest": True},
                                           dedup_key="digest") is not None:
            enqueued += 1
    return enqueued


def _set(db: Database, job: QueueJob, **fields) -> None:
    db[QueueJob.COLLECTION].update_one({"_id": job.id}, {"$set": fields})


def _check_lane(lane: str) -> None:
    if lane not in LANES:
        raise ValueError(f"{lane!r} is not one of {LANES}")
