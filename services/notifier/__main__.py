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

# A 5xx from api.telegram.org is Telegram being briefly unwell, not our request being wrong.
#
# **Two digests were lost permanently to a "Bad Gateway".** Only 429 was retried here, so a 502
# raised on the first response; the queue then spent all five of its attempts inside five and a
# half minutes, and dead-lettered while the outage was still going. Both the retries and the
# dead-letter were technically working — they were just measuring Telegram's bad minute at a
# resolution far finer than the outage (ROADMAP 6ad).
#
# So retry in here, where the waits are seconds apart rather than instant, before spending one of
# the job's attempts. 4xx other than 429 stays immediate: a malformed payload or a revoked bot does
# not improve by asking again.
SERVER_ERROR_RETRIES = 3
SERVER_ERROR_BACKOFF_SEC = 2.0

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
    # Two separate budgets, counted separately, because they are two different conditions: a flood
    # wait is Telegram pacing us, a 502 is Telegram broken. One shared counter would let a burst of
    # 429s exhaust the allowance an outage needs.
    rate_limits = 0
    server_errors = 0
    while rate_limits < RATE_LIMIT_RETRIES and server_errors < SERVER_ERROR_RETRIES:
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
            rate_limits += 1
            logger.warning("telegram: rate limited, waiting %.0fs (attempt %d)", wait, rate_limits)
            time.sleep(min(wait, MAX_RATE_LIMIT_WAIT_SEC))
            continue

        # Telegram's own fault and Telegram's own to fix: 500, 502, 504. Nothing about the payload
        # changes, so the only useful response is to wait and ask again.
        if response.status_code >= 500:
            server_errors += 1
            logger.warning("telegram: %d from the API, retrying in %.0fs (attempt %d)",
                           response.status_code, SERVER_ERROR_BACKOFF_SEC, server_errors)
            time.sleep(SERVER_ERROR_BACKOFF_SEC * server_errors)
            continue

        if response.status_code >= 400:
            raise RuntimeError(response.json().get("description", response.text)[:300])
        return

    # Which budget ran out, in the message — "Telegram kept rate limiting" sent an operator looking
    # at bot limits during what was actually an upstream outage.
    if server_errors:
        raise RuntimeError(
            f"Telegram answered {server_errors} server error(s) in a row; "
            "the rest will go out next cycle")
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
