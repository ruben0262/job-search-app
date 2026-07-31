"""The claim protocol. One table, four lanes, at-least-once delivery.

`SCHEDULER_PLAN.md` §3-§5 in code. The parts that matter and why:

- **`FOR UPDATE SKIP LOCKED`** is what makes two workers claiming at once safe without a lock table.
  A worker that would block on a row skips it and takes the next one instead.
- **Attempts increment on claim, not on failure.** A worker killed mid-job never gets to report the
  failure, and a counter that only moved on a reported failure would let it retry forever.
- **`finished_at` drives fairness.** Ordering by `run_after` alone is FIFO, which is fine for lanes
  whose work is naturally spread — but one user labelling a backlog would occupy `train` and `label`
  entirely. Those two lanes order by "who has waited longest since their last completion" instead.
- **Dead-lettering is loud.** After `MAX_ATTEMPTS` the row is `dead` and stays queryable. A job that
  retries forever consumes a worker slot in perpetuity and reports nothing.
"""
from __future__ import annotations

import logging
import uuid as _uuid
from collections.abc import Sequence
from datetime import timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from libs.db.models import QueueJob, User
from libs.db.models.queue import CLAIM_TIMEOUT_MIN, LANES, MAX_ATTEMPTS

logger = logging.getLogger(__name__)

# Lanes where one user can enqueue a burst, so FIFO would let them occupy the pool
# (`SCHEDULER_PLAN.md` §5). `notify` and `docgen` are naturally spread and stay FIFO — the fairness
# subquery is not free, and paying for it where it changes nothing is just latency.
FAIR_LANES = frozenset({"train", "label"})


def lane_paused(session: Session, lane: str) -> bool:
    """Whether an operator has paused this lane (`deploy/scripts/pipeline_switch.py`).

    The table is created on demand by that script and will usually not exist, so a missing table is
    "nothing is paused" rather than an error. Reading it must never be the thing that breaks
    enqueuing — a switch whose absence stops the platform is worse than no switch.

    **Asked with `to_regclass` rather than by catching the failure.** A missing relation aborts the
    surrounding transaction in Postgres, and the obvious `except` then has to roll back to recover
    — which throws away the caller's uncommitted work. That is not a hypothetical: it deleted the
    user row out from under thirty-four tests before this was written the other way round.
    """
    if session.scalar(text("SELECT to_regclass('lane_pauses')")) is None:
        return False
    return session.scalar(
        text("SELECT true FROM lane_pauses WHERE lane = :lane"), {"lane": lane}) is True


def enqueue(session: Session, lane: str, user_id: _uuid.UUID, *,
            payload: dict | None = None, dedup_key: str | None = None,
            delay_min: int = 0) -> int | None:
    """Add work. Returns the job id, or `None` when an identical pending job already exists.

    The collapse is a partial unique index, not an application check — five labels arriving at once
    must produce one retrain even when they race, and application-level "does one exist?" cannot
    promise that.
    """
    _check_lane(lane)
    if lane_paused(session, lane):
        # Refused at the door rather than claimed and dropped. A paused lane must not accumulate
        # work it will process in a burst on resume — that is how a pause turns into an incident of
        # its own — and the caller treats `None` as "already pending", which is the right shape.
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
    """Take the next ready job in this lane, or `None`.

    `SKIP LOCKED` is the whole protocol: a concurrent claimer skips the row this one has locked
    rather than blocking on it, so N workers claim N distinct jobs with no coordination.
    """
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
    # `populate_existing` because the claim was a raw UPDATE: without it, a session that has seen
    # this row before returns the identity-mapped copy, whose `attempts` and `state` predate the
    # claim. A worker loop is exactly that kind of long-lived session, so it would decide against a
    # retry count that never moves — and a job that always kills its worker would retry forever.
    return session.get(QueueJob, row, populate_existing=True)


def complete(session: Session, job: QueueJob) -> None:
    """Mark done. **Commit this in the same transaction as the job's own effect.**

    That is what makes at-least-once delivery survivable: the `seen` row and "job done" either both
    land or neither does, so a retry after a crash re-sends nothing already recorded
    (`SCHEDULER_PLAN.md` §4).
    """
    job.state = "done"
    job.finished_at = func.now()
    job.last_error = None


def fail(session: Session, job: QueueJob, error: str, *,
         retry_in_min: int = 1, max_attempts: int = MAX_ATTEMPTS) -> str:
    """Record a failure. Returns the resulting state: `ready` (will retry) or `dead`.

    Attempts were already incremented at claim time, so this decides against a count that includes
    workers that died without reporting anything.
    """
    job.last_error = error[:2000]
    if job.attempts >= max_attempts:
        job.state = "dead"
        job.finished_at = func.now()
        return "dead"
    job.state = "ready"
    job.claimed_at = None
    job.claimed_by = None
    job.run_after = _now_plus(retry_in_min)
    return "ready"


def terminate(session: Session, job: QueueJob, reason: str) -> None:
    """Dead-letter immediately, without spending the remaining attempts.

    For conditions that will never succeed — a revoked Telegram token being the one that actually
    happens (`SCHEDULER_PLAN.md` §6, G6). Retrying those five times wastes attempts and then goes
    quiet anyway, which is the worst of both.
    """
    job.state = "dead"
    job.finished_at = func.now()
    job.last_error = reason[:2000]


def sweep_expired(session: Session, lane: str | None = None) -> int:
    """Return claims older than the lane's visibility timeout to `ready`. Returns how many.

    A worker killed mid-job costs one retry and never a lost job. Run it on a timer; it is cheap and
    idempotent, and nothing else notices a dead worker.
    """
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
    """Enqueue a `notify` job for every user whose feed is due, and advance their next slot.

    **`next_run_at` advances at enqueue, not at completion.** A slow or failed delivery must not
    stall a user's feed forever — that is the single most likely silent failure on the platform and
    the one a user would notice last (`SCHEDULER_PLAN.md` §4, §6).

    Users whose bot is `broken` are skipped: retrying a revoked token cannot succeed, and the fix is
    a reconnect email, not a queue entry.
    """
    due = select(User.id).where(
        User.active.is_(True), User.status == "active",
        User.telegram_status == "connected",
        User.next_run_at.is_not(None), User.next_run_at <= func.now())

    # `rowcount` is not dependable for INSERT ... SELECT ... RETURNING, so the returned ids are
    # counted directly. A dispatcher that reports a number it did not measure is worse than one
    # that reports nothing.
    enqueued = len(session.execute(
        pg_insert(QueueJob)
        .from_select(["lane", "user_id", "dedup_key", "payload"],
                     select(text("'notify'"), User.id, text("'due'"), text("'{}'::jsonb"))
                     .where(User.id.in_(due)))
        .on_conflict_do_nothing(index_elements=["lane", "user_id", "dedup_key"],
                                index_where=text("state IN ('ready','claimed')"))
        .returning(QueueJob.id)).scalars().all())

    session.execute(
        update(User)
        .where(User.id.in_(due))
        .values(next_run_at=_now_plus_column(User.notify_interval_min)))
    return enqueued


def jobs_today(session: Session, lane: str, user_id: _uuid.UUID) -> int:
    """How many jobs this user has enqueued on `lane` since their own local midnight.

    Counted from the queue rather than from what the work produced, which is the difference between
    a quota and a scoreboard: a CV build that failed still spent the Claude call, and a limit that
    only counted successes would let a broken account retry all day on the user's money.

    In `users.timezone`, not UTC, for the same reason the notification cap is: "ten a day, resets at
    midnight" has to keep meaning the same thing in November and for somebody three zones away.
    """
    _check_lane(lane)
    timezone = session.scalar(select(User.timezone).where(User.id == user_id)) or "UTC"
    local_midnight = func.date_trunc("day", func.timezone(timezone, func.now()))
    return session.scalar(
        select(func.count()).select_from(QueueJob)
        .where(QueueJob.lane == lane, QueueJob.user_id == user_id,
               func.timezone(timezone, QueueJob.created_at) >= local_midnight)) or 0


def _now_plus(minutes: int):
    """`now() + N minutes`, server-side.

    `make_interval` rather than an interpolated `interval '...'` literal: SQLAlchemy cannot type a
    raw `text()` on the right of an addition (it raises at construction), and building the literal
    by string formatting would put a caller-supplied number into SQL text.
    """
    return func.now() + func.make_interval(0, 0, 0, 0, 0, int(minutes)) if minutes else func.now()


def _now_plus_column(column):
    """`now() + <column> minutes`, where the interval comes from a per-user column."""
    return func.now() + func.make_interval(0, 0, 0, 0, 0, column)


def _fair_order(lane: str) -> str:
    """Round-robin: the user who has waited longest since their last completion in this lane.

    Combined with duplicate collapse, this is what stops one user occupying the trainer
    (`SCHEDULER_PLAN.md` §5, T6).
    """
    return ("(SELECT COALESCE(MAX(j2.finished_at), 'epoch') FROM jobs j2 "
            " WHERE j2.user_id = jobs.user_id AND j2.lane = jobs.lane AND j2.state = 'done'), "
            "run_after")


def _check_lane(lane: str) -> None:
    if lane not in LANES:
        raise ValueError(f"{lane!r} is not one of {LANES}")
