"""Telegram, through the platform's one bot: a link that connects in one tap, and the webhook behind it.

The person taps a `t.me/<bot>?start=<code>` link, Telegram opens the bot, and pressing Start sends
`/start <code>` here; redeeming the code ties that chat to their account. Every reply rides in the
answer to Telegram's own call (the Bot API's "reply to the webhook"), so this service never holds the
bot token and a reply cannot fail apart from the request it answers.
"""
from __future__ import annotations

import hmac
import logging
import os
import uuid as _uuid
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel
from pymongo.database import Database

from libs.auth import consume_token, issue_token
from libs.db import get
from libs.db.models import User
from libs.notify import site_url
from libs.obs import audit
from services.api.deps import current_user, get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/telegram", tags=["telegram"])


class TelegramLink(BaseModel):
    url: str


class TelegramStatus(BaseModel):
    status: str


class TelegramReply(BaseModel):
    """One message back to the chat, which Telegram sends for us."""

    method: Literal["sendMessage"] = "sendMessage"
    chat_id: int
    text: str


@router.post("/link")
def link(db: Database = Depends(get_db),
         user_id: _uuid.UUID = Depends(current_user)) -> TelegramLink:
    """A one-time link that opens the platform's bot carrying a code for this account."""
    bot = os.environ.get("TELEGRAM_BOT_USERNAME", "").lstrip("@")
    if not bot:
        logger.warning("telegram: TELEGRAM_BOT_USERNAME is not set, so %s could not connect", user_id)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Telegram is not set up on this site yet.")
    return TelegramLink(url=f"https://t.me/{bot}?start={issue_token(db, user_id, 'telegram_connect')}")


@router.delete("/link")
def unlink(db: Database = Depends(get_db),
           user_id: _uuid.UUID = Depends(current_user)) -> TelegramStatus:
    """Stop sending to Telegram. Connecting again is the same one tap."""
    db[User.COLLECTION].update_one({"_id": user_id}, {"$set": {"telegram_chat_id": None,
                                                                "telegram_status": "unconfigured"}})
    audit(logger, "telegram.disconnected", user=user_id)
    return TelegramStatus(status="unconfigured")


@router.post("/webhook")
async def webhook(request: Request, db: Database = Depends(get_db),
                  x_telegram_bot_api_secret_token: str | None = Header(default=None),
                  ) -> TelegramReply | None:
    """Everything anybody sends the bot: `/start <code>` connects, anything else points at the site."""
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
    # 404 rather than 401, so a probe learns nothing; `compare_digest`, so timing tells it nothing.
    if not (secret and x_telegram_bot_api_secret_token
            and hmac.compare_digest(x_telegram_bot_api_secret_token, secret)):
        logger.warning("telegram: a webhook call without the right secret header was refused")
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")

    message = (await request.json()).get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    if chat_id is None:
        # Not a message; Telegram only needs the 200.
        return None
    command, _, code = (message.get("text") or "").strip().partition(" ")
    if command.split("@")[0] != "/start" or not code:
        return TelegramReply(chat_id=chat_id,
                             text=f"I send you new jobs that match you. Everything else is on {site_url()}")

    user_id = consume_token(db, code.strip(), "telegram_connect")
    user = get(db, User, user_id) if user_id else None
    if user is None:
        connected = db[User.COLLECTION].count_documents(
            {"telegram_chat_id": str(chat_id), "telegram_status": "connected"}, limit=1)
        return TelegramReply(chat_id=chat_id, text=(
            "You are already connected. New jobs that match you arrive here." if connected else
            f"That link has expired. Tap Connect Telegram on {site_url()} for a fresh one."))

    db[User.COLLECTION].update_one({"_id": user.id}, {"$set": {"telegram_chat_id": str(chat_id),
                                                                "telegram_status": "connected"}})
    audit(logger, "telegram.connected", user=user.id)
    return TelegramReply(chat_id=chat_id,
                         text="Connected! New jobs that match you will arrive here as they are found.")
