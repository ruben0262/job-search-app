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
import time

import httpx

from libs.db.models import QueueJob
from libs.db.session import session_scope
from libs.obs import configure
from libs.queue.queue import dispatch_due_notifications
from libs.queue.worker import Terminal, run_worker
from libs.secrets import SecretStore, install
from services.notifier.deliver import BotRevoked, deliver

TELEGRAM_TIMEOUT_SEC = 20.0

# How many times one message may be re-offered after a 429 before it waits for the next cycle.
# Telegram's own answer is obeyed each time, so this bounds the wait rather than the politeness.
RATE_LIMIT_RETRIES = 3

# The longest a single 429 may hold a worker. Telegram occasionally answers with minutes during a
# flood-wait; holding the queue slot that long starves every other user of this worker, and the
# undelivered rows are simply picked up on the next run.
MAX_RATE_LIMIT_WAIT_SEC = 60.0

logger = logging.getLogger(__name__)


def send(token: str, chat_id: str, message: str, keyboard: dict | None = None) -> None:
    """One message to one chat, optionally with buttons. Raises with Telegram's own description.

    The description matters: `deliver` decides terminal-versus-transient from its text, and a
    generic "HTTP 403" would lose the distinction between a blocked bot and a rate limit.
    """
    # **HTML, because the card is HTML.** `<b>85</b>` and an escaped `&` in a company name are the
    # whole reason the reference's cards read as cards; without the parse mode they arrive as raw
    # markup. Every field is escaped in `services/notifier/card.py`.
    payload = {"chat_id": chat_id, "text": message, "disable_web_page_preview": True,
               "parse_mode": "HTML"}
    if keyboard:
        payload["reply_markup"] = keyboard
    for attempt in range(1, RATE_LIMIT_RETRIES + 1):
        response = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
            timeout=TELEGRAM_TIMEOUT_SEC)

        # **429 is Telegram telling us how long to wait, not a failure.** It answers with
        # `parameters.retry_after` in seconds; obeying it turns a burst into "slower", while
        # ignoring it is how a bot gets restricted. Read rather than guessed: a fixed backoff is
        # either too short to help or longer than it had to be.
        if response.status_code == 429:
            body = response.json() if response.content else {}
            wait = float((body.get("parameters") or {}).get("retry_after", 1))
            logger.warning("telegram: rate limited, waiting %.0fs (attempt %d)", wait, attempt)
            time.sleep(min(wait, MAX_RATE_LIMIT_WAIT_SEC))
            continue

        if response.status_code >= 400:
            raise RuntimeError(response.json().get("description", response.text)[:300])
        return

    raise RuntimeError("Telegram kept rate limiting this bot; the rest will go out next cycle")


def handle(session, job: QueueJob) -> None:
    try:
        sent = deliver(session, job.user_id, store=SecretStore(session), send=send)
    except BotRevoked as exc:
        # `deliver` already flagged the account. Dead-lettering rather than retrying is the point:
        # five attempts against a revoked token end in silence anyway (G6).
        raise Terminal(str(exc)) from exc
    logger.info("notified %s with %d item(s)", job.user_id, sent)


def main(argv: list[str] | None = None) -> int:
    configure("notifier")
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
