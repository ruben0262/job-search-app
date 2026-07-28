"""The `score` lane worker.

    python -m services.scorer

One job is one batch of 500 rows through an already-loaded model, re-enqueued while rows remain.
Free to run — CPU only, no credential, no provider — which is why this lane has no cost ceiling and
the label lane does.

**A missing model is not a failure.** A new account has no artifact until its first train, and the
harvest cycle enqueues for everybody; dead-lettering those would fill the queue with red for the one
state every account passes through. `ModelUnavailable` ends the job quietly and the next cycle asks
again.
"""
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
        # Quietly, and at info: "no model trained yet" is the normal state of a new account, and
        # logging it as an error would teach whoever reads the logs to skim them.
        logger.info("score: nothing to do for %s — %s", job.user_id, exc)
        return

    if remaining:
        # Release the dedup slot before claiming it again: `enqueue` collapses against any job in
        # `ready` *or* `claimed` with the same key, and this job holds exactly the key its
        # continuation needs. Left alone the chain stops after one batch and looks like success.
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
