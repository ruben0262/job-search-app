"""The worker loop."""
from __future__ import annotations

import contextlib
import logging
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from pymongo.database import Database

from libs.db.models import QueueJob
from libs.queue.queue import claim, complete, fail, sweep_expired, terminate

logger = logging.getLogger(__name__)

# How long to wait when a lane is empty.
IDLE_SLEEP_SEC = 5.0

# The same, for the lanes a person is watching. A tap on Build my CV, or a message in a setup chat,
# waits this long before the job is even claimed — five seconds of it is the largest single delay
# between the tap and anything happening, and it is spent doing nothing.
#
# A quarter of a second since documents render in a browser that is already running (2026-10-28):
# at 1 s the wait was 0.62 s of a 0.89 s instant CV, median, and up to 1.0 s of it. Four claims a
# second on an empty lane is an index lookup each.
INTERACTIVE_IDLE_SLEEP_SEC = 0.25

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


def run_once(db: Database, lane: str, worker: str,
             handler: Callable[[Database, QueueJob], None],
             stats: WorkerStats) -> bool:
    """Claim and run at most one job. What the handler wrote before failing stays written: each write
    is its own, so a delivery that stops part-way keeps the sends it made rather than repeating them."""
    job = claim(db, lane, worker)
    if job is None:
        return False

    stats.claimed += 1
    try:
        handler(db, job)
        complete(db, job)
        stats.completed += 1
    except Terminal as exc:
        terminate(db, job, str(exc))
        stats.dead += 1
        logger.warning("%s job %s: terminal — %s", lane, job.id, exc)
    except Exception as exc:
        state = fail(db, job, f"{type(exc).__name__}: {exc}")
        stats.failed += 1
        stats.dead += state == "dead"
        stats.errors[type(exc).__name__] = stats.errors.get(type(exc).__name__, 0) + 1
        logger.warning("%s job %s failed (%s): %s", lane, job.id, state, exc, exc_info=True)
    return True


def run_worker(db: Database, lane: str, worker: str,
               handler: Callable[[Database, QueueJob], None], *,
               max_jobs: int | None = None,
               idle_sleep: float = IDLE_SLEEP_SEC,
               stop: Callable[[], bool] | None = None,
               periodic: Callable[[Database], None] | None = None,
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
            stats.swept += sweep_expired(db, lane)
            last_sweep = now

        if periodic is not None and now - last_periodic > periodic_every:
            last_periodic = now
            try:
                periodic(db)
            except Exception:
                logger.exception("%s: the periodic pass failed; the lane keeps running", lane)

        if not run_once(db, lane, worker, handler, stats):
            time.sleep(idle_sleep)
    return stats


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
