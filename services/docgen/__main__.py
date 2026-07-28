"""The `docgen` lane worker.

    python -m services.docgen

One queue job is one CV: two Claude calls at most, two Chrome compiles, one Telegram upload, and a
scratch directory that does not outlive the job. ~90–130 s, which is why this lane has its own
claim timeout and its own container.

**The credential is decrypted here and nowhere else in this service.** `SecretStore.use` holds it
for the duration of the build and `redacting` covers the traceback an SDK raises on a bad key —
the same discipline the labeller follows, for the same reason: an API key in a log file is a key
that has to be rotated.

**A build that cannot succeed dead-letters immediately.** No confirmed profile, no description on
the posting, no Telegram chat, hidden content in the output: none of those change on a retry, and
retrying spends the user's money five times to arrive at the same refusal.
"""
from __future__ import annotations

import logging
import os

import httpx

from libs.db.models import QueueJob
from libs.db.session import session_scope
from libs.llm import CLAUDE_KINDS, LlmError, client_for
from libs.obs import configure
from libs.queue.worker import Terminal, run_worker
from libs.secrets import SecretStore, install, redacting
from services.docgen.pdf import CompileFailed
from services.docgen.run import NotBuildable, run_build

logger = logging.getLogger(__name__)

# Longer than the notifier's 20s: this is a multi-megabyte upload rather than a text message, and a
# timeout here means a built CV that the user never receives.
UPLOAD_TIMEOUT_SEC = 120.0


def send_document(token: str, chat_id: str, content: bytes, filename: str,
                  caption: str = "") -> None:
    """Upload one PDF to one chat. Raises with Telegram's own description.

    `sendDocument` rather than `sendMessage` with a link: there is no link. The document exists for
    the length of this call and is deleted afterwards (D9), so the upload is the delivery.
    """
    response = httpx.post(
        f"https://api.telegram.org/bot{token}/sendDocument",
        data={"chat_id": chat_id, "caption": caption[:1024]},
        files={"document": (filename, content, "application/pdf")},
        timeout=UPLOAD_TIMEOUT_SEC)
    if response.status_code >= 400:
        raise RuntimeError(response.json().get("description", response.text)[:300])


def handle(session, job: QueueJob) -> None:
    """Build the CV this job asks for, on the account's own Claude credential."""
    job_id = (job.payload or {}).get("job_id")
    if not isinstance(job_id, int):
        raise Terminal(f"docgen job {job.id} carries no corpus job id")

    store = SecretStore(session)
    with store.use(job.user_id, "telegram_bot") as bot:
        if bot is None:
            raise Terminal("no Telegram bot token stored — the CV would have nowhere to go")
        bot_token = bot.reveal()

        for kind in CLAUDE_KINDS:
            with store.use(job.user_id, kind) as secret:
                if secret is None:
                    continue
                value = secret.reveal()
                with redacting(value, bot_token):
                    result = _build(session, job, client_for(value, kind), bot_token, job_id)
            logger.info("docgen: built %s for %s in %d call(s), %s page(s)",
                        result.job_id, job.user_id, result.calls, result.pages)
            return

    raise Terminal("no Claude credential stored — a CV build needs the user's own key or token")


def _build(session, job: QueueJob, client, bot_token: str, job_id: int):
    """The failure taxonomy, in one place.

    `CompileFailed` is deliberately *not* terminal: Chrome runs out of memory on a busy box and
    succeeds on the next attempt, which is the whole reason this service has a memory limit and a
    retry budget rather than one or the other.
    """
    try:
        return run_build(session, job.user_id, job_id, client=client,
                         send_document=send_document, token=bot_token)
    except (NotBuildable, LlmError) as exc:
        raise Terminal(str(exc)) from exc
    except CompileFailed as exc:
        logger.warning("docgen: compile failed for user %s, will retry: %s", job.user_id, exc)
        raise


def main() -> int:
    configure("docgen")
    install()
    run_worker(session_scope, "docgen", os.environ.get("WORKER_NAME", "docgen-1"), handle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
