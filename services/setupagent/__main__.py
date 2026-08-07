"""The `setup` lane: one chat turn, run where nothing can interrupt it."""
from __future__ import annotations

import logging
import os

from libs.agents.loop import run_turn
from libs.db.models import QueueJob, SetupMessage
from libs.db.session import session_scope
from libs.llm import CLAUDE_KINDS, client_for
from libs.obs import audit, configure
from libs.queue.worker import INTERACTIVE_IDLE_SLEEP_SEC, Terminal, run_worker
from libs.secrets import SecretStore, install, redacting

logger = logging.getLogger(__name__)

# What the reader is told when the turn could not run at all.
NO_CREDENTIAL = ("Your Claude credential is missing or was rejected, so this turn could not run. "
                 "Add it under Your details and say that again.")


def handle(session, job: QueueJob) -> None:
    """Run one queued turn into the pending row the API created."""
    reply_id = (job.payload or {}).get("message_id")
    reply_row = session.get(SetupMessage, reply_id) if reply_id else None
    if reply_row is None or reply_row.user_id != job.user_id:
        # The conversation was deleted while the turn was queued.
        logger.info("setup: reply row %s is gone; dropping the turn", reply_id)
        return
    # `cancelled` joins the finished states: redelivering a stopped turn would spend the credential.
    if reply_row.status in ("done", "failed", "cancelled"):
        # A retry after the turn already finished.
        logger.info("setup: reply row %s is already %s; nothing to do", reply_id, reply_row.status)
        return

    payload = job.payload or {}
    store = SecretStore(session)
    for kind in CLAUDE_KINDS:
        with store.use(job.user_id, kind) as secret:
            if secret is None:
                continue
            value = secret.reveal()
            with redacting(value):
                try:
                    result = run_turn(session, job.user_id,
                                      client=client_for(value, kind),
                                      message=payload.get("message", ""),
                                      conversation_id=reply_row.conversation_id,
                                      agent=payload.get("agent", "setup"),
                                      reply_row=reply_row)
                except Exception as exc:
                    _fail(session, reply_row,
                          "That turn did not finish. Nothing you said was lost — say it again, "
                          "or rephrase it if this keeps happening.")
                    # Terminal rather than a retry: a second attempt fails the same way.
                    raise Terminal(f"setup turn failed: {type(exc).__name__}") from exc

        audit(logger, "setup.turn", user=job.user_id, agent=payload.get("agent", "setup"),
              tools=len(result["tools_run"]), files=len(result["files_written"]))
        return

    _fail(session, reply_row, NO_CREDENTIAL)
    raise Terminal("no Claude credential stored — the setup chat runs on the user's own key")


def _fail(session, reply_row: SetupMessage, detail: str) -> None:
    """Say so in the transcript."""
    reply_row.status = "failed"
    reply_row.error = detail
    if not reply_row.content:
        reply_row.content = detail
    session.commit()


def main() -> int:
    configure("setupagent")
    install()
    run_worker(session_scope, "setup", os.environ.get("WORKER_NAME", "setupagent-1"), handle,
               idle_sleep=INTERACTIVE_IDLE_SLEEP_SEC)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
