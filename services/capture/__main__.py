"""The `process` lane worker."""
from __future__ import annotations

import logging
import os

from sqlalchemy import func

import libs.notify as notifylib
from libs.db.models import QueueJob, User
from libs.db.session import session_scope
from libs.llm import CLAUDE_KINDS, client_for
from libs.obs import audit, configure
from libs.queue.worker import Terminal, run_worker
from libs.secrets import SecretStore, install, redacting
from services.capture.process import CaptureFailed, process_user

logger = logging.getLogger(__name__)

def handle(session, job: QueueJob) -> None:
    """Produce a draft profile for one user. Terminal on anything a retry cannot fix."""
    store = SecretStore(session)
    for kind in CLAUDE_KINDS:
        with store.use(job.user_id, kind) as secret:
            if secret is None:
                continue
            value = secret.reveal()
            with redacting(value):
                try:
                    draft = process_user(session, job.user_id, client_for(value, kind))
                except CaptureFailed as exc:
                    # Terminal, not a retry: the same documents and prompt fail the same way.
                    notifylib.notify(session, job.user_id, notifylib.PROCESSING_FAILED,
                                     "The documents could not be turned into a profile. You can "
                                     "enter it by hand instead, or fix the documents and retry.")
                    raise Terminal(str(exc)) from exc

        user = session.get(User, job.user_id)
        user.processed_at = func.now()
        audit(logger, "process.completed", user=job.user_id, source="capture",
              not_list=len(draft.not_list))
        return

    raise Terminal("no Claude credential stored — processing needs the user's own key or token")


def main() -> int:
    configure("capture")
    install()
    run_worker(session_scope, "process", os.environ.get("WORKER_NAME", "capture-1"), handle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
