"""The `label` lane worker."""
from __future__ import annotations

import logging
import os

from libs.db.models import QueueJob, User
from libs.db.session import session_scope
from libs.llm import CLAUDE_KINDS, LlmError, client_for
from libs.obs import audit, configure
from libs.queue import enqueue
from libs.queue.worker import Terminal, run_worker
from libs.secrets import SecretStore, install, redacting
from services.labeller.coldstart import next_batch, ready_to_train

logger = logging.getLogger(__name__)


def handle(session, job: QueueJob) -> None:
    """Do one batch of whatever this account needs, then queue the follow-up."""
    store = SecretStore(session)
    for kind in CLAUDE_KINDS:
        with store.use(job.user_id, kind) as secret:
            if secret is None:
                continue
            value = secret.reveal()
            with redacting(value):
                try:
                    did, written, remaining = next_batch(session, job.user_id,
                                                         client_for(value, kind))
                except LlmError as exc:
                    raise Terminal(str(exc)) from exc

        _queue_follow_up(session, job, did, written, remaining)
        logger.info("%s: %d written for %s, %d remaining", did, written, job.user_id, remaining)
        return

    raise Terminal("no Claude credential stored — labelling needs the user's own key or token")


def _queue_follow_up(session, job: QueueJob, did: str, written: int, remaining: int) -> None:
    """Another batch if this one made progress, otherwise a train.

    **`written` is what makes this safe to re-arm on.** The chain used to continue on `remaining`
    alone, which is a statement about the target rather than about whether anything happened — so a
    batch that wrote nothing would either stop forever (when the empty pool reported `remaining=0`)
    or re-queue itself forever (now that it reports the truth). Neither is wanted: a batch that
    wrote nothing has nothing left to do *this minute*, and the harvest tick brings the account
    back once the corpus has grown. That is the same shape the score lane was fixed into.
    """
    if written and remaining and did != "train":
        # Release the dedup slot before claiming it again.
        job.dedup_key = None
        session.flush()
        # Checked before re-arming, which is the only place it can be checked.
        user = session.get(User, job.user_id)
        if user is not None and user.labelling_paused:
            logger.info("cold_start: paused by the user, not re-arming for %s", job.user_id)
            return
        enqueue(session, "label", job.user_id, dedup_key="label")
        return

    if ready_to_train(session, job.user_id):
        enqueue(session, "train", job.user_id, dedup_key="train")
        audit(logger, "labelling.complete", user=job.user_id, next="train")


def main() -> int:
    configure("labeller")
    install()
    run_worker(session_scope, "label", os.environ.get("WORKER_NAME", "labeller-1"), handle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
