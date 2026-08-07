"""The worker loop."""
from __future__ import annotations

import contextlib
import logging
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from libs.db.models import QueueJob
from libs.queue.queue import claim, complete, fail, sweep_expired, terminate

logger = logging.getLogger(__name__)

# How long to wait when a lane is empty.
IDLE_SLEEP_SEC = 5.0

# The same, for the lanes a person is watching. A tap on Build my CV, or a message in a setup chat,
# waits this long before the job is even claimed — five seconds of it is the largest single delay
# between the tap and anything happening, and it is spent doing nothing.
INTERACTIVE_IDLE_SLEEP_SEC = 1.0

# Expired claims are swept from inside the loop rather than by a separate cron.
SWEEP_EVERY_SEC = 60.0


class Terminal(RuntimeError):
    """Raise from a handler for a condition that will never succeed. Dead-letters immediately."""


@dataclass
class WorkerStats:
    claimed: int = 0
    completed: int = 0
    failed: int = 0
    dead: int = 0
    swept: int = 0
    errors: dict[str, int] = field(default_factory=dict)


def run_once(session: Session, lane: str, worker: str,
             handler: Callable[[Session, QueueJob], None],
             stats: WorkerStats) -> bool:
    """Claim and run at most one job."""
    job = claim(session, lane, worker)
    if job is None:
        session.commit()
        return False

    stats.claimed += 1
    job_id = job.id
    # Captured now: `session.rollback()` below undoes the claim, and the retry budget must survive.
    attempts = job.attempts
    try:
        handler(session, job)
        complete(session, job)
        session.commit()
        stats.completed += 1
    except Terminal as exc:
        session.rollback()
        # Bound to a local first: Python unbinds the `except` name at the end of the block.
        reason = str(exc)
        _record(session, job_id, attempts, lambda s, j: terminate(s, j, reason))
        stats.dead += 1
        logger.warning("%s job %s: terminal — %s", lane, job_id, exc)
    except Exception as exc:
        session.rollback()
        message = f"{type(exc).__name__}: {exc}"
        state = _record(session, job_id, attempts, lambda s, j: fail(s, j, message))
        stats.failed += 1
        stats.dead += state == "dead"
        stats.errors[type(exc).__name__] = stats.errors.get(type(exc).__name__, 0) + 1
        logger.warning("%s job %s failed (%s): %s", lane, job_id, state, exc, exc_info=True)
    return True


def run_worker(session_factory: Callable[[], Session], lane: str, worker: str,
               handler: Callable[[Session, QueueJob], None], *,
               max_jobs: int | None = None,
               idle_sleep: float = IDLE_SLEEP_SEC,
               stop: Callable[[], bool] | None = None,
               periodic: Callable[[Session], None] | None = None,
               periodic_every: float = SWEEP_EVERY_SEC) -> WorkerStats:
    """Drain `lane` until told to stop."""
    stats = WorkerStats()
    should_stop = stop or _sigterm_flag()
    last_sweep = 0.0
    last_periodic = 0.0

    while not should_stop():
        if max_jobs is not None and stats.claimed >= max_jobs:
            break

        now = time.monotonic()
        if now - last_sweep > SWEEP_EVERY_SEC:
            with session_factory() as session:
                stats.swept += sweep_expired(session, lane)
                session.commit()
            last_sweep = now

        if periodic is not None and now - last_periodic > periodic_every:
            last_periodic = now
            try:
                with session_factory() as session:
                    periodic(session)
                    session.commit()
            except Exception:
                logger.exception("%s: the periodic pass failed; the lane keeps running", lane)

        with session_factory() as session:
            worked = run_once(session, lane, worker, handler, stats)
        if not worked:
            time.sleep(idle_sleep)
    return stats


def _record(session: Session, job_id: int, attempts: int,
            write: Callable[[Session, QueueJob], object]) -> object:
    """Write a failure in a clean transaction — the handler's is unusable after a rollback."""
    job = session.get(QueueJob, job_id, populate_existing=True)
    if job is None:
        return None
    job.attempts = attempts
    result = write(session, job)
    session.commit()
    return result


def _sigterm_flag() -> Callable[[], bool]:
    """Stop at the next job boundary on SIGTERM, rather than mid-handler."""
    stopping = False

    def _handle(_signum, _frame):
        nonlocal stopping
        stopping = True
        logger.info("worker: SIGTERM received, finishing the current job")

    # ValueError when not on the main thread; the caller passes its own `stop` in that case.
    with contextlib.suppress(ValueError):
        signal.signal(signal.SIGTERM, _handle)
    return lambda: stopping
