"""The `score` lane worker."""
from __future__ import annotations

import logging
import os

from libs.db.models import QueueJob
from libs.db.session import session_scope
from libs.obs import configure
from libs.queue import enqueue
from libs.queue.worker import run_worker
from libs.secrets import install
from services.scorer.score_lane import ModelUnavailable, score_batch

logger = logging.getLogger(__name__)


def handle(session, job: QueueJob) -> None:
    try:
        scored, remaining = score_batch(session, job.user_id)
    except ModelUnavailable as exc:
        # Quietly, at info: "no model trained yet" is the normal state of a new account.
        logger.info("score: nothing to do for %s — %s", job.user_id, exc)
        return

    if remaining:
        # Release the dedup slot before claiming again: `enqueue` collapses against `ready` or `claimed`.
        job.dedup_key = None
        session.flush()
        enqueue(session, "score", job.user_id, dedup_key="score")

    if scored:
        logger.info("scored %d for %s, %d remaining", scored, job.user_id, remaining)


def main() -> int:
    configure("scorer")
    install()
    run_worker(session_scope, "score", os.environ.get("WORKER_NAME", "scorer-1"), handle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
