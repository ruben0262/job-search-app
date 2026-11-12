"""The claim protocol."""
from __future__ import annotations

import logging
import uuid as _uuid
from collections.abc import Sequence
from datetime import timedelta

from pymongo import ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from libs import settings
from libs.db import find, insert, now
from libs.db.models import CorpusJob, LanePause, QueueJob, User
from libs.db.models.queue import CLAIM_TIMEOUT_MIN, LANES, MAX_ATTEMPTS
from libs.membership import members
from libs.notify import reachable
from libs.notify.digest import due

logger = logging.getLogger(__name__)


def lane_paused(db: Database, lane: str) -> bool:
    """Whether an operator has paused this lane (`deploy/scripts/pipeline_switch.py`)."""
    return db[LanePause.COLLECTION].count_documents({"_id": lane}, limit=1) > 0


def enqueue(db: Database, lane: str, user_id: _uuid.UUID, *,
            payload: dict | None = None, dedup_key: str | None = None,
            delay_min: int = 0) -> int | None:
    """Add work. `None` when the lane is paused or the same work (lane, user, dedup key) is pending."""
    _check_lane(lane)
    if lane_paused(db, lane):
        # Refused at the door rather than claimed and dropped.
        logger.info("queue: %s is paused, not enqueuing", lane)
        return None
    try:
        return insert(db, QueueJob(lane=lane, user_id=user_id, dedup_key=dedup_key, payload=payload or {},
                                   run_after=now() + timedelta(minutes=delay_min))).id
    except DuplicateKeyError:
        return None


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


def dispatch_due_notifications(db: Database) -> int:
    """Enqueue a `notify` job for every reachable account the corpus has grown for since its last one.

    **Nothing scores any more** (ROADMAP Phase 8), so there is no "match waiting" row to look for:
    what is new for a person is the notify job's own question, answered by `libs.notify.owed`. This
    pass only decides whether asking is worth a claim — a posting stored after the account's last
    finished notify run — so a quiet corpus enqueues nothing, and a busy one enqueues one run per
    account per harvest rather than one a minute.
    """
    newest = db[CorpusJob.COLLECTION].find_one({}, {"first_seen_at": 1}, sort=[("first_seen_at", -1)])
    if newest is None:
        return 0
    # Members only (`libs.membership`): a job alert is the site's content, as the jobs page is.
    accounts = db[User.COLLECTION].distinct(
        "_id", {"$and": [{"active": True, "status": "active"}, reachable(db), members()]})
    last_run = {row["_id"]: row["last"] for row in db[QueueJob.COLLECTION].aggregate([
        {"$match": {"lane": "notify", "state": "done", "user_id": {"$in": accounts}}},
        {"$group": {"_id": "$user_id", "last": {"$max": "$finished_at"}}}])}

    enqueued = 0
    for user_id in accounts:
        if (last_run.get(user_id) is None or newest["first_seen_at"] > last_run[user_id]) and \
                enqueue(db, "notify", user_id, dedup_key="due") is not None:
            enqueued += 1
    return enqueued


def dispatch_due_digests(db: Database) -> int:
    """Enqueue a daily email for everyone whose is due (`libs.notify.digest.due`). On the same sweep as
    the alerts, so it has no clock of its own; the `digest` dedup key keeps it to one at a time."""
    hour = settings.get(db, "email_digest_hour")
    enqueued = 0
    for user in find(db, User, {"$and": [{"email_alerts": True, "active": True, "status": "active"},
                                         members()]}):
        if due(db, user, hour) and enqueue(db, "notify", user.id, payload={"digest": True},
                                           dedup_key="digest") is not None:
            enqueued += 1
    return enqueued


def _set(db: Database, job: QueueJob, **fields) -> None:
    db[QueueJob.COLLECTION].update_one({"_id": job.id}, {"$set": fields})


def _check_lane(lane: str) -> None:
    if lane not in LANES:
        raise ValueError(f"{lane!r} is not one of {LANES}")
