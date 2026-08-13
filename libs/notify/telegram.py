"""Replying to Telegram, from the service that received the webhook.

**A webhook that computes an answer and returns it to nobody is a mute button.** Every handler in
`services/api/telegram_routes.py` built a sentence for the person who tapped — "Building your CV —
it will arrive here shortly", "Only the owner of this feed can use these buttons", the over-quota
refusal, the not-ready refusal — and returned it as the HTTP response body. Telegram does not read
that body unless it names a Bot API method, so none of those sentences has ever reached a user.
Tapping a button spun briefly and did nothing; `/rate` replied with a card that was never sent.

Answering is what makes a tap feel like it worked. A build takes a minute or two to arrive, so
without the acknowledgement the only evidence a tap did anything is a PDF appearing later — and
when the tap was *refused*, there is no evidence at all.

These are best-effort. The database work is already staged by the time they run, so a failure here
must never lose the build that was queued: both log at `WARNING` and return, and the caller does
not treat a failed reply as a failed tap.
"""
from __future__ import annotations

import logging

import httpx

from libs.credentials.validate import TELEGRAM_URL

# The person is waiting on this inside their tap, so it is short. A slow reply is worth less than
# the build it is acknowledging.
TIMEOUT = 8.0

# Telegram truncates a callback answer at 200 characters and rejects a longer one.
MAX_ANSWER = 200

logger = logging.getLogger(__name__)


def answer_callback(token: str, query_id: str, text: str = "") -> bool:
    """Clear the spinner on a tapped button, showing `text` if there is any."""
    return _call(token, "answerCallbackQuery",
                 {"callback_query_id": query_id, "text": text[:MAX_ANSWER]})


def send_message(token: str, chat_id: str, text: str, keyboard: dict | None = None) -> bool:
    """Send one message to a chat, optionally carrying an inline keyboard."""
    payload: dict = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if keyboard:
        payload["reply_markup"] = keyboard
    return _call(token, "sendMessage", payload)


def _call(token: str, method: str, payload: dict) -> bool:
    """One Bot API call. Never raises — the caller's work is already done."""
    try:
        response = httpx.post(TELEGRAM_URL.format(token=token, method=method),
                              json=payload, timeout=TIMEOUT)
        if response.status_code >= 400:
            # Never the token: the URL carries it.
            logger.warning("telegram: %s returned %s — %s", method, response.status_code,
                           response.text[:200])
            return False
        return True
    except Exception as exc:  # noqa: BLE001 - the tap is already recorded; no reply is worth losing it
        logger.warning("telegram: %s failed (%s) — the tap was handled anyway", method,
                       type(exc).__name__)
        return False
