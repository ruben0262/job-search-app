"""Telegram, through the person's own bot (owner's decision, 2026-11-01): they make a bot with
@BotFather and paste its token, then press Start in it, and we find their chat from that.

Three calls, in the order the page makes them: `PUT /telegram/bot` checks and keeps the token,
`POST /telegram/check` looks for the Start (the page asks every couple of seconds until it comes),
and `POST /telegram/test` sends one message on demand. `DELETE /telegram/bot` forgets all of it.
"""
from __future__ import annotations

import logging
import uuid as _uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from pymongo.database import Database

from libs import telegram
from libs.db import get, now
from libs.db.models import User
from libs.notify import site_url
from libs.obs import audit
from libs.secrets import SecretStore, redacting
from services.api.deps import current_user, get_db, get_store, rate_limit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/telegram", tags=["telegram"])

WELCOME = "Connected! New jobs that match your search will arrive here as they are found."


class BotToken(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class TelegramState(BaseModel):
    status: Literal["unconfigured", "waiting", "connected", "broken"]
    # Their bot's @name, once its token is saved.
    bot: str | None
    # Opens their bot in Telegram, where they press Start.
    open_url: str | None


def _state(user: User) -> TelegramState:
    bot = user.telegram_bot_name
    return TelegramState(status=user.telegram_status, bot=bot,
                         open_url=f"https://t.me/{bot}" if bot else None)


def _set(db: Database, user_id: _uuid.UUID, **fields) -> User:
    db[User.COLLECTION].update_one({"_id": user_id}, {"$set": fields})
    return get(db, User, user_id)


@router.get("")
def state(db: Database = Depends(get_db), user_id: _uuid.UUID = Depends(current_user)) -> TelegramState:
    """Where their Telegram is."""
    return _state(get(db, User, user_id))


@router.put("/bot")
def save_bot(body: BotToken, request: Request, db: Database = Depends(get_db),
             store: SecretStore = Depends(get_store),
             user_id: _uuid.UUID = Depends(current_user)) -> TelegramState:
    """Check a bot token with Telegram and keep it. Their chat comes next, from pressing Start."""
    rate_limit(request, f"telegram:{user_id}", limit=20, per_seconds=3600)
    token = body.token.strip()
    if not telegram.BOT_TOKEN_SHAPE.match(token):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="That does not look like a bot token. Copy the whole line BotFather "
                                   "sent after “Use this token to access the HTTP API”.")
    with redacting(token):
        try:
            name = telegram.bot_name(token)
        except telegram.TelegramError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail=f"Telegram did not accept that token: {exc}") from exc
        store.put(user_id, "telegram_bot", token, validated=True)
    audit(logger, "telegram.bot_saved", user=user_id)
    return _state(_set(db, user_id, telegram_bot_name=name, telegram_status="waiting", telegram_chat_id=None,
                       telegram_connected_at=None))


@router.post("/check")
def check(db: Database = Depends(get_db), store: SecretStore = Depends(get_store),
          user_id: _uuid.UUID = Depends(current_user)) -> TelegramState:
    """Has Start been pressed in their bot? If so, that chat is theirs, and it gets a welcome."""
    user = get(db, User, user_id)
    if user.telegram_status == "connected":
        return _state(user)
    with store.use(user_id, "telegram_bot") as secret:
        if secret is None:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="Add your bot's token first.")
        token = secret.reveal()
        try:
            chat = telegram.started_chat(token)
            if chat is None:
                return _state(user)
            telegram.send(token, chat, WELCOME)
        except (telegram.TelegramError, RuntimeError) as exc:
            logger.warning("telegram: could not reach %s's bot while connecting: %s", user_id, exc)
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"Telegram said: {exc}") from exc
    audit(logger, "telegram.connected", user=user_id)
    return _state(_set(db, user_id, telegram_chat_id=chat, telegram_status="connected",
                       telegram_connected_at=now()))


@router.post("/test")
def test(db: Database = Depends(get_db), store: SecretStore = Depends(get_store),
         user_id: _uuid.UUID = Depends(current_user)) -> TelegramState:
    """Send one message, to see that it arrives."""
    user = get(db, User, user_id)
    if user.telegram_status not in ("connected", "broken") or not user.telegram_chat_id:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Connect your bot first.")
    with store.use(user_id, "telegram_bot") as secret:
        if secret is None:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="Add your bot's token first.")
        try:
            telegram.send(secret.reveal(), user.telegram_chat_id,
                          f"This is a test from Mahoraga. Your jobs are at {site_url()}")
        except RuntimeError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"Telegram said: {exc}") from exc
    # A test that arrived proves a chat marked broken works again.
    return _state(_set(db, user_id, telegram_status="connected"))


@router.delete("/bot")
def forget_bot(db: Database = Depends(get_db), store: SecretStore = Depends(get_store),
               user_id: _uuid.UUID = Depends(current_user)) -> TelegramState:
    """Stop sending to Telegram and forget the bot."""
    store.delete(user_id, "telegram_bot")
    audit(logger, "telegram.disconnected", user=user_id)
    return _state(_set(db, user_id, telegram_status="unconfigured", telegram_chat_id=None,
                       telegram_bot_name=None, telegram_connected_at=None))
