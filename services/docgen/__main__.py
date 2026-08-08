"""The `docgen` lane worker."""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager

import httpx

import libs.notify as notifylib
from libs.db.models import QueueJob
from libs.db.session import session_scope
from libs.llm import CLAUDE_KINDS, LlmError, client_for
from libs.obs import configure
from libs.queue.worker import INTERACTIVE_IDLE_SLEEP_SEC, Terminal, run_worker
from libs.secrets import SecretStore, install, redacting
from services.docgen.pdf import CompileFailed
from services.docgen.run import NotBuildable, run_build

logger = logging.getLogger(__name__)

# Longer than the notifier's 20s: this is a multi-megabyte upload rather than a text message.
UPLOAD_TIMEOUT_SEC = 120.0


def send_document(token: str, chat_id: str, content: bytes, filename: str,
                  caption: str = "") -> None:
    """Upload one PDF to one chat."""
    response = httpx.post(
        f"https://api.telegram.org/bot{token}/sendDocument",
        data={"chat_id": chat_id, "caption": caption[:1024]},
        files={"document": (filename, content, "application/pdf")},
        timeout=UPLOAD_TIMEOUT_SEC)
    if response.status_code >= 400:
        raise RuntimeError(response.json().get("description", response.text)[:300])


def handle(session, job: QueueJob) -> None:
    """Build the CV this job asks for, on the account's own Claude credential."""
    payload = job.payload or {}
    job_id = payload.get("job_id")
    if not isinstance(job_id, int):
        raise Terminal(f"docgen job {job.id} carries no corpus job id")
    # `cv` unless the tap said otherwise.
    kind = payload.get("kind") or "cv"

    store = SecretStore(session)
    with store.use(job.user_id, "telegram_bot") as bot:
        if bot is None:
            raise Terminal("no Telegram bot token stored — the CV would have nowhere to go")
        bot_token = bot.reveal()

        for credential in CLAUDE_KINDS:
            with store.use(job.user_id, credential) as secret:
                if secret is None:
                    continue
                value = secret.reveal()
                with redacting(value, bot_token), _http_for(store, job.user_id) as http:
                    result = _build(session, job, client_for(value, credential), bot_token,
                                    job_id=job_id, kind=kind, http=http)
            # `None` is `NotBuildable`: the account was told why and there is nothing to retry.
            # Reading `result.calls` regardless raised `AttributeError`, which the worker saw as a
            # crash rather than as the deliberate non-failure `_build` had already handled — so a
            # user with no confirmed profile got a job that retried every sixty seconds and told
            # him again every time, for as long as anybody left it alone.
            if result is None:
                return
            logger.info("docgen: built the %s for %s in %d call(s), %s page(s)",
                        kind, job.user_id, result.calls, result.pages)
            return

    raise Terminal("no Claude credential stored — a CV build needs the user's own key or token")


@contextmanager
def _http_for(store: SecretStore, user_id):
    """An HTTP client for the pre-build freshness check, on this user's own proxy if they have one."""
    with store.use(user_id, "proxy") as secret:
        if secret is None:
            with httpx.Client(timeout=25, follow_redirects=True) as client:
                yield client
            return
        value = secret.reveal()
        with redacting(value), httpx.Client(timeout=25, follow_redirects=True,
                                            proxy=value) as client:
            yield client


def _build(session, job: QueueJob, client, bot_token: str, *, job_id: int, kind: str,
           http=None):
    """The failure taxonomy, in one place."""
    try:
        return run_build(session, job.user_id, job_id, client=client,
                         send_document=send_document, token=bot_token, kind=kind, http=http)
    except NotBuildable as exc:
        # Not a failure, and it must not be recorded as one.
        notifylib.notify(session, job.user_id, notifylib.DOCUMENT_NOT_READY, str(exc))
        logger.info("docgen: %s cannot build yet — %s", job.user_id, exc)
        return None
    except LlmError as exc:
        # Their credential, and the operator should see a run of these.
        notifylib.notify(session, job.user_id, notifylib.DOCUMENT_NOT_READY,
                         "Your Claude credential could not build this document.")
        raise Terminal(str(exc)) from exc
    except CompileFailed as exc:
        logger.warning("docgen: compile failed for user %s, will retry: %s", job.user_id, exc)
        raise


def main() -> int:
    configure("docgen")
    install()
    run_worker(session_scope, "docgen", os.environ.get("WORKER_NAME", "docgen-1"), handle,
               idle_sleep=INTERACTIVE_IDLE_SLEEP_SEC)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
