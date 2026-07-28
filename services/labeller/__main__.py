"""The `label` lane worker.

    python -m services.labeller

One queue job is one batch of 50 jobs, one Haiku call, on the user's own credential. The worker
re-enqueues itself while stale labels remain, which is what makes a four-thousand-job relabel
eighty small jobs rather than one hour-long one — and that is what lets the queue's round-robin
serve a second user's first batch before this user's second.

**The credential is decrypted here and nowhere else.** `SecretStore.use` holds it for the duration
of the call and the redaction filter covers the traceback an SDK raises on a bad key.
"""
from __future__ import annotations

import logging
import os

from libs.db.models import QueueJob
from libs.db.session import session_scope
from libs.llm import CLAUDE_KINDS, LlmError, client_for
from libs.obs import configure
from libs.queue import enqueue
from libs.queue.worker import Terminal, run_worker
from libs.secrets import SecretStore, install, redacting
from services.labeller.label import relabel_user

logger = logging.getLogger(__name__)


def handle(session, job: QueueJob) -> None:
    """Relabel one batch, then re-enqueue if more remain."""
    store = SecretStore(session)
    for kind in CLAUDE_KINDS:
        with store.use(job.user_id, kind) as secret:
            if secret is None:
                continue
            with redacting(secret.reveal()):
                try:
                    written, remaining = relabel_user(
                        session, job.user_id, client_for(secret.reveal(), kind))
                except LlmError as exc:
                    raise Terminal(str(exc)) from exc

        if remaining:
            # **Release the dedup slot before claiming it again.** `enqueue` collapses against any
            # job in `ready` or `claimed` with the same key — and this job is `claimed`, holding
            # exactly the key the continuation needs. Left alone the continuation is silently
            # dropped and the relabel stops half-finished, which looks like success.
            #
            # Clearing it here keeps the collapse doing its real job: a user pressing the button
            # again mid-chain still folds into the pending continuation rather than starting a
            # second chain on their own money.
            job.dedup_key = None
            session.flush()
            # Enqueued inside the handler's transaction, so the follow-up and this batch's labels
            # land together — or neither does.
            enqueue(session, "label", job.user_id, dedup_key="relabel")
        logger.info("relabelled %d for %s, %d remaining", written, job.user_id, remaining)
        return

    raise Terminal("no Claude credential stored — relabelling needs the user's own key or token")


def main() -> int:
    configure("labeller")
    install()
    run_worker(session_scope, "label", os.environ.get("WORKER_NAME", "labeller-1"), handle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
