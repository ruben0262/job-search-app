"""The `notify` lane worker, its two transports (Telegram and Web Push), and the sweep that feeds it."""
from __future__ import annotations

import json
import logging
import os
import sys
import time

import httpx
from pywebpush import WebPushException, webpush

from libs.db import database, ensure_schema
from libs.db.models import PushSubscription, QueueJob
from libs.obs import configure
from libs.queue.queue import dispatch_due_notifications
from libs.queue.worker import run_worker
from libs.secrets import install
from services.notifier.deliver import deliver

# One bound for either transport's request, so a slow one cannot outlast the run's send budget alone.
SEND_TIMEOUT_SEC = 20.0

# How long a push service holds an alert for a phone that is asleep or offline: a day, because a job
# alert is still worth reading the next morning.
PUSH_TTL_SEC = 24 * 3600

# How many times one message may be re-offered after a 429 before it waits for the next cycle.
RATE_LIMIT_RETRIES = 3

# The longest a single 429 may hold a worker.
MAX_RATE_LIMIT_WAIT_SEC = 60.0

# A 5xx from api.telegram.org is Telegram being briefly unwell, not our request being wrong.
SERVER_ERROR_RETRIES = 3
SERVER_ERROR_BACKOFF_SEC = 2.0

# How often the sweep asks "has the corpus grown for anybody since their last run"
# (`libs.queue.queue`). A posting stored now reaches a person within about this long.
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
            timeout=SEND_TIMEOUT_SEC)

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


def push(subscription: PushSubscription, payload: dict) -> bool:
    """One alert to one browser. False when its push service says the subscription is gone."""
    try:
        webpush({"endpoint": subscription.endpoint,
                 "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth}},
                json.dumps(payload),
                vapid_private_key=os.environ["VAPID_PRIVATE_KEY"],
                # A fresh dict every call: `webpush` writes the audience and expiry into it.
                vapid_claims={"sub": os.environ["VAPID_SUBJECT"]},
                timeout=SEND_TIMEOUT_SEC, ttl=PUSH_TTL_SEC)
    except WebPushException as exc:
        # 404 and 410 are an unsubscribed or expired browser. Anything else is a fault, and raised.
        if exc.status_code in (404, 410):
            return False
        raise
    return True


def _dispatch(db) -> None:
    """The sweep: anybody the corpus has grown for since their last run. Silent when it finds none."""
    enqueued = dispatch_due_notifications(db)
    if enqueued:
        logger.info("sweep: enqueued %d notify job(s)", enqueued)


def handle(db, job: QueueJob) -> None:
    """One person's run, through whichever transports the platform has keys for."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    vapid = os.environ.get("VAPID_PRIVATE_KEY") and os.environ.get("VAPID_SUBJECT")
    deliver(db, job.user_id,
            telegram=(lambda chat, text, buttons: send(token, chat, text, buttons)) if token else None,
            push=push if vapid else None)


def main(argv: list[str] | None = None) -> int:
    configure("notifier")
    install()
    ensure_schema()

    if "--tick" in (argv if argv is not None else sys.argv[1:]):
        enqueued = dispatch_due_notifications(database())
        logger.info("dispatcher: enqueued %d notify job(s)", enqueued)
        return 0

    # The dispatcher runs inside the worker, not on its own timer.
    run_worker(database(), "notify", os.environ.get("WORKER_NAME", "notifier-1"), handle,
               periodic=_dispatch, periodic_every=DISPATCH_EVERY_SEC)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
