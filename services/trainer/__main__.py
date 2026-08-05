"""The `train` lane worker. Concurrency 1, CPU-fenced by the container.

    python -m services.trainer

Queue concurrency alone is not enough: one fit can saturate every core through BLAS threading, so
the thread caps are set here as well as in the container (`SCHEDULER_PLAN.md` §2). Setting them
before numpy is imported is the only point at which they take effect.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

from libs.db.models import QueueJob  # noqa: E402
from libs.db.session import session_scope  # noqa: E402
from libs.obs import configure  # noqa: E402
from libs.queue.worker import Terminal, run_worker  # noqa: E402
from libs.secrets import install  # noqa: E402
from services.trainer.train import NotEnoughData, should_retrain, train_user  # noqa: E402

ARTIFACT_DIR = Path(os.environ.get("MODEL_DIR", "/var/lib/jobsearch/models"))


def handle(session, job: QueueJob) -> None:
    if not should_retrain(session, job.user_id):
        # **Not a failure, and it must not be recorded as one.** This said `raise Terminal(...)`,
        # which dead-letters — so every label added after a model was already current produced a
        # red "1 job(s) dead-lettered in the train lane" on the operator's health page, about the
        # trainer correctly deciding there was nothing to fit (ROADMAP 6ah).
        #
        # The job is complete. It asked "is a retrain worth it", the answer was no, and the answer
        # is logged where an operator can find it rather than paged.
        logging.getLogger(__name__).info(
            "trainer: %s has no new labels since its active model — nothing to fit", job.user_id)
        return
    try:
        version = train_user(session, job.user_id, artifact_dir=ARTIFACT_DIR)
    except NotEnoughData as exc:
        raise Terminal(str(exc)) from exc
    logging.getLogger(__name__).info("trained %s -> model_version %s", job.user_id, version)


def main() -> int:
    configure("trainer")
    install()
    run_worker(session_scope, "train", os.environ.get("WORKER_NAME", "trainer-1"), handle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
