"""The `label` lane worker.

    python -m services.labeller

One queue job is one batch of 50 jobs, one Haiku call, on the user's own credential. The worker
re-enqueues itself while work remains, which turns a four-thousand-job relabel into eighty small
jobs rather than one hour-long one — and that is what lets the queue's round-robin serve a second
user's first batch before this user's second.

**One job kind, and the worker decides what it means.** Carrying existing labels across a rubric
change and buying a first set for a new account are the same lane, the same call, and the same
money; which one an account needs is a fact about its rows, not something a caller should have to
know (`services/labeller/coldstart.py`). When the labels are in, it queues the train itself — an
account sitting on 300 fresh labels and no model because nobody pressed a third button is not a
state to leave somebody in.

**The credential is decrypted here and nowhere else.** `SecretStore.use` holds it for the duration
of the call and the redaction filter covers the traceback an SDK raises on a bad key.
"""
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

        _queue_follow_up(session, job, did, remaining)
        logger.info("%s: %d written for %s, %d remaining", did, written, job.user_id, remaining)
        return

    raise Terminal("no Claude credential stored — labelling needs the user's own key or token")


def _queue_follow_up(session, job: QueueJob, did: str, remaining: int) -> None:
    """Another batch if there is more to do, otherwise a train.

    Both are enqueued inside the handler's transaction, so the follow-up and this batch's labels
    land together — or neither does, and the queue replays the batch.
    """
    if remaining and did != "train":
        # **Release the dedup slot before claiming it again.** `enqueue` collapses against any job
        # in `ready` *or* `claimed` with the same key, and this job is `claimed`, holding exactly
        # the key its continuation needs. Left alone the continuation is silently dropped and the
        # run stops after one batch — which looks identical to success.
        #
        # Clearing it keeps the collapse doing its real job: a user pressing the button again
        # mid-run folds into the pending continuation rather than starting a second run on their
        # own money.
        job.dedup_key = None
        session.flush()
        # **Checked before re-arming, which is the only place it can be checked.** This loop is
        # what keeps labelling alive, so a stop that does not reach here does not stop anything.
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
