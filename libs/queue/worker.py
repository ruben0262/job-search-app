"""The worker loop. One per lane, one process each.

The loop itself is uninteresting on purpose — claim, run, commit — but two details are not:

1. **The handler's effect and the job's completion commit together.** That is what makes
   at-least-once delivery survivable: the `seen` row, or the `model_version` bump, either lands with
   "job done" or neither lands (`SCHEDULER_PLAN.md` §3-§4). A handler that commits its own
   transaction breaks this, so handlers are given the session and must not commit.
2. **A failed handler rolls back its effect but must still record the failure.** The failure is
   written in a fresh transaction, because the one the handler poisoned cannot be used for anything.
"""
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

# How long to wait when a lane is empty. Short enough that a queued job is not visibly delayed, long
# enough that an idle platform is not hammering Postgres with claim attempts.
IDLE_SLEEP_SEC = 5.0

# Expired claims are swept from inside the loop rather than by a separate cron: one fewer thing to
# deploy, and a lane with no worker running has nothing to recover anyway.
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
    """Claim and run at most one job. Returns whether there was work.

    The caller owns the session; this commits it — once per job, covering both the handler's effect
    and the completion.
    """
    job = claim(session, lane, worker)
    if job is None:
        session.commit()
        return False

    stats.claimed += 1
    job_id = job.id
    try:
        handler(session, job)
        complete(session, job)
        session.commit()
        stats.completed += 1
    except Terminal as exc:
        session.rollback()
        # Bound to a local first: Python unbinds the `except` name at the end of the block, so a
        # closure over it is a latent NameError the moment this is refactored.
        reason = str(exc)
        _record(session, job_id, lambda s, j: terminate(s, j, reason))
        stats.dead += 1
        logger.warning("%s job %s: terminal — %s", lane, job_id, exc)
    except Exception as exc:
        session.rollback()
        message = f"{type(exc).__name__}: {exc}"
        state = _record(session, job_id, lambda s, j: fail(s, j, message))
        stats.failed += 1
        stats.dead += state == "dead"
        stats.errors[type(exc).__name__] = stats.errors.get(type(exc).__name__, 0) + 1
        logger.warning("%s job %s failed (%s): %s", lane, job_id, state, exc, exc_info=True)
    return True


def run_worker(session_factory: Callable[[], Session], lane: str, worker: str,
               handler: Callable[[Session, QueueJob], None], *,
               max_jobs: int | None = None,
               idle_sleep: float = IDLE_SLEEP_SEC,
               stop: Callable[[], bool] | None = None) -> WorkerStats:
    """Drain `lane` until told to stop. `max_jobs` bounds it, which is what the tests use.

    A fresh session per iteration rather than one held open for the process's life: a long-lived
    session accumulates an identity map of every job it has ever seen, and a connection held idle
    across a Postgres restart fails on the next claim rather than reconnecting.
    """
    stats = WorkerStats()
    should_stop = stop or _sigterm_flag()
    last_sweep = 0.0

    while not should_stop():
        if max_jobs is not None and stats.claimed >= max_jobs:
            break

        now = time.monotonic()
        if now - last_sweep > SWEEP_EVERY_SEC:
            with session_factory() as session:
                stats.swept += sweep_expired(session, lane)
                session.commit()
            last_sweep = now

        with session_factory() as session:
            worked = run_once(session, lane, worker, handler, stats)
        if not worked:
            time.sleep(idle_sleep)
    return stats


def _record(session: Session, job_id: int, write: Callable[[Session, QueueJob], object]) -> object:
    """Write a failure in a clean transaction — the handler's is unusable after a rollback."""
    job = session.get(QueueJob, job_id)
    if job is None:
        return None
    result = write(session, job)
    session.commit()
    return result


def _sigterm_flag() -> Callable[[], bool]:
    """Stop at the next job boundary on SIGTERM, rather than mid-handler.

    A worker killed mid-job is recoverable — the sweeper returns the claim — but it costs the user a
    retry and, on the `train` lane, a wasted fit. Draining first is free.
    """
    stopping = False

    def _handle(_signum, _frame):
        nonlocal stopping
        stopping = True
        logger.info("worker: SIGTERM received, finishing the current job")

    # ValueError when not on the main thread; the caller passes its own `stop` in that case.
    with contextlib.suppress(ValueError):
        signal.signal(signal.SIGTERM, _handle)
    return lambda: stopping
