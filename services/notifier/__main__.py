"""The `notify` lane worker, and the dispatcher tick that feeds it."""
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
RATE_LIMIT_RETRIES = 3

# The longest a single 429 may hold a worker.
MAX_RATE_LIMIT_WAIT_SEC = 60.0

# A 5xx from api.telegram.org is Telegram being briefly unwell, not our request being wrong.
SERVER_ERROR_RETRIES = 3
SERVER_ERROR_BACKOFF_SEC = 2.0

# How often the sweep asks "is anybody holding a match nobody queued". Not the delivery cadence —
# there is none: the score lane enqueues a run the moment it records a match (`libs.queue.queue`).
DISPATCH_EVERY_SEC = 60.0

logger = logging.getLogger(__name__)


def send(token: str, chat_id: str, message: str, keyboard: dict | None = None) -> None:
    """One message to one chat, optionally with buttons."""
    # HTML, because the card is HTML.
    payload = {"chat_id": chat_id, "text": message, "disable_web_page_preview": True,
               "parse_mode": "HTML"}
    if keyboard:
        payload["reply_markup"] = keyboard
    # Two budgets counted separately: a flood wait is Telegram pacing us, a 502 is Telegram broken.
    rate_limits = 0
    server_errors = 0
    while rate_limits < RATE_LIMIT_RETRIES and server_errors < SERVER_ERROR_RETRIES:
        response = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
            timeout=TELEGRAM_TIMEOUT_SEC)

        # 429 is Telegram telling us how long to wait, not a failure.
        if response.status_code == 429:
            body = response.json() if response.content else {}
            wait = float((body.get("parameters") or {}).get("retry_after", 1))
            rate_limits += 1
            logger.warning("telegram: rate limited, waiting %.0fs (attempt %d)", wait, rate_limits)
            time.sleep(min(wait, MAX_RATE_LIMIT_WAIT_SEC))
            continue

        # Telegram's own fault and Telegram's own to fix: 500, 502, 504.
        if response.status_code >= 500:
            server_errors += 1
            logger.warning("telegram: %d from the API, retrying in %.0fs (attempt %d)",
                           response.status_code, SERVER_ERROR_BACKOFF_SEC, server_errors)
            time.sleep(SERVER_ERROR_BACKOFF_SEC * server_errors)
            continue

        if response.status_code >= 400:
            raise RuntimeError(response.json().get("description", response.text)[:300])
        return

    # Which budget ran out, in the message: the two send an operator to different places.
    if server_errors:
        raise RuntimeError(
            f"Telegram answered {server_errors} server error(s) in a row; "
            "the rest will go out next cycle")
    raise RuntimeError("Telegram kept rate limiting this bot; the rest will go out next cycle")


def _dispatch(session) -> None:
    """The sweep: anything scored and unsent that nothing has queued. Silent when it finds none."""
    enqueued = dispatch_due_notifications(session)
    if enqueued:
        logger.info("sweep: enqueued %d notify job(s) nothing else had queued", enqueued)


def handle(session, job: QueueJob) -> None:
    try:
        sent = deliver(session, job.user_id, store=SecretStore(session), send=send)
    except BotRevoked as exc:
        # `deliver` already flagged the account.
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

    # The dispatcher runs inside the worker, not on its own timer.
    run_worker(session_scope, "notify", os.environ.get("WORKER_NAME", "notifier-1"), handle,
               periodic=_dispatch, periodic_every=DISPATCH_EVERY_SEC)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
