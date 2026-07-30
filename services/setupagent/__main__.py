"""The `setup` lane: one chat turn, run where nothing can interrupt it.

**A turn used to be an HTTP request that took minutes.** Switching tabs, opening another chat,
refreshing or signing out all aborted it — the user's message was already committed, the reply
never arrived, and nothing said why. That is the bug this service exists to remove.

The API writes the user's message and a *pending* assistant row, enqueues, and answers immediately.
This worker fills that row in: streaming text into it as the model produces it, then marking it
done. The browser polls the transcript, so what it renders is the same row whether somebody
watched it arrive or came back to it the next morning.

**A failed turn is written down, not swallowed.** The row goes to `failed` with a sentence, because
a chat that silently stops answering is indistinguishable from a chat that is still thinking.
"""
from __future__ import annotations

import logging
import os

from libs.agents.loop import run_turn
from libs.db.models import QueueJob, SetupMessage
from libs.db.session import session_scope
from libs.llm import CLAUDE_KINDS, client_for
from libs.obs import audit, configure
from libs.queue.worker import Terminal, run_worker
from libs.secrets import SecretStore, install, redacting

logger = logging.getLogger(__name__)

# What the reader is told when the turn could not run at all. Deliberately plain: every one of
# these is something the user can act on, and none of them is their fault.
NO_CREDENTIAL = ("Your Claude credential is missing or was rejected, so this turn could not run. "
                 "Add it under Your details and say that again.")


def handle(session, job: QueueJob) -> None:
    """Run one queued turn into the pending row the API created."""
    reply_id = (job.payload or {}).get("message_id")
    reply_row = session.get(SetupMessage, reply_id) if reply_id else None
    if reply_row is None or reply_row.user_id != job.user_id:
        # The conversation was deleted while the turn was queued. Nothing to fill in, and nothing
        # broken — the user threw the question away themselves.
        logger.info("setup: reply row %s is gone; dropping the turn", reply_id)
        return
    if reply_row.status in ("done", "failed"):
        # A retry after the turn already finished. At-least-once delivery means this is normal, and
        # re-running it would spend the user's money twice for one question.
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
                    # Terminal rather than a retry: a second attempt spends the user's credential
                    # again on a call that already failed once for a reason retrying cannot fix.
                    raise Terminal(f"setup turn failed: {type(exc).__name__}") from exc

        audit(logger, "setup.turn", user=job.user_id, agent=payload.get("agent", "setup"),
              tools=len(result["tools_run"]), files=len(result["files_written"]))
        return

    _fail(session, reply_row, NO_CREDENTIAL)
    raise Terminal("no Claude credential stored — the setup chat runs on the user's own key")


def _fail(session, reply_row: SetupMessage, detail: str) -> None:
    """Say so in the transcript. Committed on its own, because the exception that follows is what
    dead-letters the job and must not take the explanation with it."""
    reply_row.status = "failed"
    reply_row.error = detail
    if not reply_row.content:
        reply_row.content = detail
    session.commit()


def main() -> int:
    configure("setupagent")
    install()
    run_worker(session_scope, "setup", os.environ.get("WORKER_NAME", "setupagent-1"), handle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
