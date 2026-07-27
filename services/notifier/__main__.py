"""The `notify` lane worker, and the dispatcher tick that feeds it.

    python -m services.notifier            # worker
    python -m services.notifier --tick     # one dispatcher pass, for the timer

Split so the dispatcher can run on a 60-second timer without a process holding a connection open
between ticks, while the worker stays long-lived at concurrency 8.
"""
from __future__ import annotations

import logging
import os
import sys

import httpx

from libs.db.models import QueueJob
from libs.db.session import session_scope
from libs.queue.queue import dispatch_due_notifications
from libs.queue.worker import Terminal, run_worker
from libs.secrets import SecretStore, install
from services.notifier.deliver import BotRevoked, deliver

TELEGRAM_TIMEOUT_SEC = 20.0

logger = logging.getLogger(__name__)


def send(token: str, chat_id: str, message: str) -> None:
    """One digest to one chat. Raises with Telegram's own description on failure.

    The description matters: `deliver` decides terminal-versus-transient from its text, and a
    generic "HTTP 403" would lose the distinction between a blocked bot and a rate limit.
    """
    response = httpx.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
        timeout=TELEGRAM_TIMEOUT_SEC)
    if response.status_code >= 400:
        raise RuntimeError(response.json().get("description", response.text)[:300])


def handle(session, job: QueueJob) -> None:
    try:
        sent = deliver(session, job.user_id, store=SecretStore(session), send=send)
    except BotRevoked as exc:
        # `deliver` already flagged the account. Dead-lettering rather than retrying is the point:
        # five attempts against a revoked token end in silence anyway (G6).
        raise Terminal(str(exc)) from exc
    logger.info("notified %s with %d item(s)", job.user_id, sent)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    install()

    if "--tick" in (argv if argv is not None else sys.argv[1:]):
        with session_scope() as session:
            enqueued = dispatch_due_notifications(session)
        logger.info("dispatcher: enqueued %d notify job(s)", enqueued)
        return 0

    run_worker(session_scope, "notify", os.environ.get("WORKER_NAME", "notifier-1"), handle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
