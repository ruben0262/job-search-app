"""Telegram's Bot API, for a person's own bot: what it is called, which chat started it, and sending.

Each person makes their own bot with @BotFather and gives us its token (owner's decision,
2026-11-01). Pressing Start in it is how we learn their chat: `started_chat` reads the bot's recent
messages, so nobody has to look a chat id up.
"""
from __future__ import annotations

import logging
import re
import time

import httpx

logger = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"

# What @BotFather hands out: the bot's number, a colon, and a secret.
BOT_TOKEN_SHAPE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")

# One bound for either transport's request, so a slow one cannot outlast the run's send budget alone.
SEND_TIMEOUT_SEC = 20.0

# How many times one message may be re-offered after a 429 before it waits for the next cycle.
RATE_LIMIT_RETRIES = 3

# The longest a single 429 may hold a worker.
MAX_RATE_LIMIT_WAIT_SEC = 60.0

# A 5xx from api.telegram.org is Telegram being briefly unwell, not our request being wrong.
SERVER_ERROR_RETRIES = 3
SERVER_ERROR_BACKOFF_SEC = 2.0



class TelegramError(RuntimeError):
    """Telegram refused, with its own words for why."""


def call(token: str, method: str, **params) -> dict | list:
    """One Bot API call, answered with its `result`."""
    response = httpx.post(API.format(token=token, method=method), json=params, timeout=SEND_TIMEOUT_SEC)
    body = response.json() if response.content else {}
    if not body.get("ok"):
        raise TelegramError(str(body.get("description") or f"Telegram answered {response.status_code}")[:300])
    return body["result"]


def bot_name(token: str) -> str:
    """The bot's @name, which also proves the token is a live one."""
    return str(call(token, "getMe")["username"])


def started_chat(token: str) -> str | None:
    """The private chat that most recently wrote to this bot — pressing Start writes /start — or None
    while nobody has. Telegram keeps a bot's unread messages for a day."""
    for update in reversed(call(token, "getUpdates", allowed_updates=["message"])):
        chat = (update.get("message") or {}).get("chat") or {}
        if chat.get("type") == "private":
            return str(chat["id"])
    return None


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
