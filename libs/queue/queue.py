"""The claim protocol."""
from __future__ import annotations

import logging
import uuid as _uuid
from collections.abc import Sequence
from datetime import timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from libs.db.models import QueueJob, User, UserJobState
from libs.db.models.queue import CLAIM_TIMEOUT_MIN, LANES, MAX_ATTEMPTS

logger = logging.getLogger(__name__)

# Lanes where one user can enqueue a burst, so FIFO would let them occupy the pool (`SCHEDULER_PLAN.md` §5).
FAIR_LANES = frozenset({"train", "label"})


def lane_paused(session: Session, lane: str) -> bool:
    """Whether an operator has paused this lane (`deploy/scripts/pipeline_switch.py`)."""
    if session.scalar(text("SELECT to_regclass('lane_pauses')")) is None:
        return False
    return session.scalar(
        text("SELECT true FROM lane_pauses WHERE lane = :lane"), {"lane": lane}) is True


def enqueue(session: Session, lane: str, user_id: _uuid.UUID, *,
            payload: dict | None = None, dedup_key: str | None = None,
            delay_min: int = 0) -> int | None:
    """Add work."""
    _check_lane(lane)
    if lane_paused(session, lane):
        # Refused at the door rather than claimed and dropped.
        logger.info("queue: %s is paused, not enqueuing", lane)
        return None
    result = session.execute(
        pg_insert(QueueJob)
        .values(lane=lane, user_id=user_id, dedup_key=dedup_key,
                payload=payload or {}, run_after=_now_plus(delay_min))
        .on_conflict_do_nothing(index_elements=["lane", "user_id", "dedup_key"],
                                index_where=text("state IN ('ready','claimed')"))
        .returning(QueueJob.id))
    return result.scalar()


def claim(session: Session, lane: str, worker: str) -> QueueJob | None:
    """Take the next ready job in this lane, or `None`."""
    _check_lane(lane)
    order = _fair_order(lane) if lane in FAIR_LANES else "run_after"
    row = session.execute(
        text(f"""
            UPDATE jobs SET state = 'claimed', claimed_at = now(),
                            claimed_by = :worker, attempts = attempts + 1
            WHERE id = (
                SELECT id FROM jobs
                WHERE lane = :lane AND state = 'ready' AND run_after <= now()
                ORDER BY {order}
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING id
        """),  # noqa: S608 - `order` is built from a fixed literal, never from caller input
        {"lane": lane, "worker": worker}).scalar()
    if row is None:
        return None
    # `populate_existing` because the claim was a raw UPDATE: the mapped copy predates it.
    return session.get(QueueJob, row, populate_existing=True)


def complete(session: Session, job: QueueJob) -> None:
    """Mark done."""
    job.state = "done"
    job.finished_at = func.clock_timestamp()
    job.last_error = None


def fail(session: Session, job: QueueJob, error: str, *,
         retry_in_min: int = 1, max_attempts: int = MAX_ATTEMPTS) -> str:
    """Record a failure."""
    job.last_error = error[:2000]
    if job.attempts >= max_attempts:
        job.state = "dead"
        job.finished_at = func.clock_timestamp()
        return "dead"
    job.state = "ready"
    job.claimed_at = None
    job.claimed_by = None
    job.run_after = _now_plus(retry_in_min)
    return "ready"


def terminate(session: Session, job: QueueJob, reason: str) -> None:
    """Dead-letter immediately, without spending the remaining attempts."""
    job.state = "dead"
    job.finished_at = func.clock_timestamp()
    job.last_error = reason[:2000]


def sweep_expired(session: Session, lane: str | None = None) -> int:
    """Return claims older than the lane's visibility timeout to `ready`."""
    lanes: Sequence[str] = (lane,) if lane else LANES
    recovered = 0
    for name in lanes:
        _check_lane(name)
        cutoff = func.now() - timedelta(minutes=CLAIM_TIMEOUT_MIN[name])
        recovered += session.execute(
            update(QueueJob)
            .where(QueueJob.lane == name, QueueJob.state == "claimed",
                   QueueJob.claimed_at < cutoff)
            .values(state="ready", claimed_at=None, claimed_by=None)).rowcount or 0
    return recovered


def dispatch_due_notifications(session: Session) -> int:
    """Enqueue a `notify` job for every account with a match waiting. The recovery sweep.

    **There is no delivery cadence.** A scored match is sent as soon as it exists: the score lane
    enqueues the notify job in the same transaction that records the score, and a run that fills its
    limit queues its own continuation. This pass is the sweep behind those two — it catches a match
    written by something other than the score lane (a relabel, an operator's `score_user.py`) and an
    account whose notify job died. It enqueues nothing when there is nothing waiting.
    """
    from libs.scoring.thresholds import cutoff_for  # noqa: PLC0415 - avoids a cycle at import time
    from libs.tenant.filters import filters_for, pending_match_conditions  # noqa: PLC0415
    from libs.tenant.repository import TenantRepository  # noqa: PLC0415

    tenant = TenantRepository(session)
    enqueued = retired = 0

    # Whole rows rather than ids: `filters_for` needs the account's own limits, and re-fetching it
    # per call is the per-user query multiplier this loop already has too much of.
    for user in session.scalars(
            select(User).where(User.active.is_(True), User.status == "active",
                               User.telegram_status == "connected")):
        cutoff = cutoff_for(session, user.id)
        # Before queueing anything: a match the pre-score filter would no longer admit is one this
        # lane failed to send in time, and sending it late is worse than not sending it. Judged by
        # that same filter, never by a window of its own — see `retire_stale_matches`.
        gone = tenant.retire_stale_matches(
            user.id, cutoff=cutoff, filters=filters_for(session, user))
        if gone:
            retired += gone
            logger.warning(
                "sweep: retired %d match(es) for %s that aged out of their own source's window "
                "while waiting — the notify lane did not keep up", gone, user.id)

        waiting = session.scalar(
            select(UserJobState.job_id)
            # The shared definition, so "waiting" cannot mean one thing here and another in the feed.
            .where(UserJobState.user_id == user.id, *pending_match_conditions(cutoff))
            .limit(1))
        if waiting is not None and enqueue(session, "notify", user.id, dedup_key="due") is not None:
            enqueued += 1
    return enqueued


def jobs_today(session: Session, lane: str, user_id: _uuid.UUID) -> int:
    """How many jobs this user has enqueued on `lane` since their own local midnight."""
    _check_lane(lane)
    timezone = session.scalar(select(User.timezone).where(User.id == user_id)) or "UTC"
    local_midnight = func.date_trunc("day", func.timezone(timezone, func.now()))
    return session.scalar(
        select(func.count()).select_from(QueueJob)
        .where(QueueJob.lane == lane, QueueJob.user_id == user_id,
               func.timezone(timezone, QueueJob.created_at) >= local_midnight)) or 0


def _now_plus(minutes: int):
    """`now() + N minutes`, server-side."""
    return func.now() + func.make_interval(0, 0, 0, 0, 0, int(minutes)) if minutes else func.now()


def _fair_order(lane: str) -> str:
    """Round-robin: the user who has waited longest since their last completion in this lane."""
    return ("(SELECT COALESCE(MAX(j2.finished_at), 'epoch') FROM jobs j2 "
            " WHERE j2.user_id = jobs.user_id AND j2.lane = jobs.lane AND j2.state = 'done'), "
            "run_after")


def _check_lane(lane: str) -> None:
    if lane not in LANES:
        raise ValueError(f"{lane!r} is not one of {LANES}")
