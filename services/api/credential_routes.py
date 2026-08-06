"""Storing, checking, and revoking the credentials a user brings."""
from __future__ import annotations

import logging
import os
import secrets
import uuid as _uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from libs.credentials import (
    telegram_channel_check,
    telegram_chat_check,
    telegram_chat_id,
    validate_credential,
)
from libs.db.models import ProxyCredential, User
from libs.db.models.user import SECRET_KINDS, TELEGRAM_ROUTE_KEYS
from libs.llm.client import CLAUDE_KINDS
from libs.obs import audit
from libs.secrets import SecretStore, redacting
from services.api.deps import current_user, get_db, get_store, rate_limit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/credentials", tags=["credentials"])


# Registering a webhook is one small call on a path a user waits on, so it fails fast.
WEBHOOK_TIMEOUT_SEC = 10.0


class CredentialBody(BaseModel):
    value: str = Field(min_length=8, max_length=4096)
    # A credential we could not reach the provider to check is stored unvalidated, not refused.
    allow_unvalidated: bool = False


@router.put("/{kind}")
def put_credential(kind: str, body: CredentialBody, request: Request, *,
                   db: Session = Depends(get_db),
                   store: SecretStore = Depends(get_store),
                   user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Store or replace one credential."""
    if kind not in SECRET_KINDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail=f"unknown credential {kind!r}")
    rate_limit(request, f"credential:{user_id}", limit=20, per_seconds=3600)

    value = body.value.strip()
    extra: dict[str, str] = {}
    with redacting(value):
        result = validate_credential(kind, value)
        if not result.ok and not body.allow_unvalidated:
            # 422 rather than 400: the request was well-formed, the credential was not.
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=result.detail)
        extra = dict(result.extra or {})

        store.put(user_id, kind, value, validated=result.ok)

        if kind == "proxy":
            # The credential row, not just the secret.
            _ensure_proxy_credential(db, user_id)

        if kind == "telegram_bot" and result.ok:
            # The chat id is read for them rather than asked for.
            chat_id = telegram_chat_id(value)
            user = db.get(User, user_id)
            if chat_id:
                user.telegram_chat_id = chat_id
                user.telegram_status = "connected"
                extra["chat_id_captured"] = "yes"
                # Their bot only learns where to send taps once we tell it, and this is the first token.
                extra["webhook"] = ensure_webhook(db, user, value)
            else:
                # Not an error: they have saved a valid token and simply not pressed Start yet.
                user.telegram_status = "unconfigured"
                extra["chat_id_captured"] = "no"

    return {"status": "saved", "validated": result.ok,
            "detail": result.detail, **extra}


def ensure_webhook(db: Session, user: User, token: str) -> str:
    """Point this user's bot at our webhook, and give their row the secret that identifies it."""
    if not user.telegram_webhook_secret:
        # 32 bytes, URL-safe: it is a path segment.
        user.telegram_webhook_secret = secrets.token_urlsafe(32)
        db.flush()

    base = os.environ.get("PUBLIC_URL", "").rstrip("/")
    if not base:
        # Loud: the alternative is a webhook pointed at localhost and buttons that fail silently.
        logger.error("telegram: PUBLIC_URL is not set, so %s's bot has no webhook to point at — "
                     "their feed buttons will not work", user.id)
        return "no_public_url"

    url = f"{base}/telegram/webhook/{user.telegram_webhook_secret}"
    try:
        response = httpx.post(
            f"https://api.telegram.org/bot{token}/setWebhook",
            json={"url": url,
                  "secret_token": user.telegram_webhook_secret,
                  # Only what we handle. Every other update type is bandwidth and log noise.
                  "allowed_updates": ["callback_query", "message"],
                  # A webhook being re-pointed should not replay taps queued against the old one.
                  "drop_pending_updates": True},
            timeout=WEBHOOK_TIMEOUT_SEC)
        body = response.json() if response.content else {}
    except httpx.HTTPError as exc:
        logger.error("telegram: could not register a webhook for %s (%s) — their feed buttons "
                     "will not work until they reconnect", user.id, exc)
        return "unreachable"

    if not body.get("ok"):
        logger.error("telegram: setWebhook refused for %s: %s", user.id,
                     body.get("description", response.text)[:200])
        return "refused"

    audit(logger, "telegram.webhook_registered", user=user.id)
    return "registered"


def _ensure_proxy_credential(db: Session, user_id) -> None:
    """Give this user a proxy credential row if they have none."""
    if db.get(ProxyCredential, user_id) is not None:
        return
    db.add(ProxyCredential(user_id=user_id, secret_ref=f"{user_id}:proxy"))
    db.flush()


@router.post("/telegram/connect")
def telegram_connect(db: Session = Depends(get_db),
                     store: SecretStore = Depends(get_store),
                     user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Re-read the chat id after the user has pressed Start."""
    user = db.get(User, user_id)
    with store.use(user_id, "telegram_bot") as token:
        if token is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                detail="save your bot token first")
        chat_id = telegram_chat_id(token.reveal())
        if not chat_id:
            return {"status": "waiting",
                    "detail": "press Start in your bot, then try again"}
        # Also here, and idempotent on purpose.
        webhook = ensure_webhook(db, user, token.reveal())

    user.telegram_chat_id = chat_id
    user.telegram_status = "connected"
    return {"status": "connected", "webhook": webhook}


class ChatBody(BaseModel):
    # A personal chat id is a positive integer.
    chat_id: str = Field(min_length=4, max_length=32, pattern=r"^\d{4,}$")


@router.put("/telegram/chat")
def set_chat(body: ChatBody, db: Session = Depends(get_db),
             store: SecretStore = Depends(get_store),
             user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Set the chat id by hand, proven by messaging it."""
    with store.use(user_id, "telegram_bot") as token:
        if token is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="save your bot token first")
        result = telegram_chat_check(token.reveal(), body.chat_id)

    if not result.ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=result.detail)

    user = db.get(User, user_id)
    user.telegram_chat_id = body.chat_id
    user.telegram_status = "connected"
    audit(logger, "credentials.chat_set", user=user_id)
    return {"status": "connected",
            "detail": "A test message just landed in your chat with the bot."}


class ChannelBody(BaseModel):
    # A channel id from @getidsbot (-100…), or a public @name. Nothing else is plausibly a channel.
    channel_id: str = Field(min_length=2, max_length=64, pattern=r"^(-100\d{4,}|@[A-Za-z0-9_]{4,})$")


@router.put("/telegram/channel")
def set_channel(body: ChannelBody, db: Session = Depends(get_db),
                store: SecretStore = Depends(get_store),
                user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Point the job stream at a channel."""
    with store.use(user_id, "telegram_bot") as token:
        if token is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="save your bot token first")
        result = telegram_channel_check(token.reveal(), body.channel_id)

    if not result.ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=result.detail)

    user = db.get(User, user_id)
    user.telegram_routes = dict.fromkeys(TELEGRAM_ROUTE_KEYS, body.channel_id)
    audit(logger, "credentials.channel_set", user=user_id)
    return {"status": "connected",
            "detail": "A test message just landed in the channel — that is where jobs stream now."}


@router.delete("/telegram/channel")
def unset_channel(db: Session = Depends(get_db),
                  user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Back to the private chat. Never a drop — the fallback chat is always valid."""
    user = db.get(User, user_id)
    user.telegram_routes = None
    return {"status": "removed", "detail": "Jobs go to your bot chat again."}


@router.get("")
def list_credentials(db: Session = Depends(get_db),
                     user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """What the account page shows: which credentials exist, never what they are."""
    from libs.tenant.repository import TenantRepository  # noqa: PLC0415 - avoids an import cycle

    stored = TenantRepository(db).secret_metadata(user_id)
    kinds = {row.kind for row in stored}
    user = db.get(User, user_id)

    # Where the jobs actually go.
    routes = user.telegram_routes or {}
    channel_id = next(iter(routes.values()), None) if routes else None

    return {
        "credentials": [
            {"kind": row.kind, "last_4": row.last_4,
             "validated": row.validated_at is not None,
             "updated_at": row.updated_at.isoformat() if row.updated_at else None}
            for row in stored],
        # The IDs are shown, not hidden.
        "telegram": {
            "status": user.telegram_status,
            "chat_id": user.telegram_chat_id,
            "channel_id": channel_id,
            "destination": "channel" if channel_id else ("chat" if user.telegram_chat_id else None),
        },
        "requirements": {
            "claude": bool(kinds & set(CLAUDE_KINDS)),
            "telegram_bot": "telegram_bot" in kinds,
            # The channel is required, not a nicety.
            "telegram_channel": channel_id is not None,
            "proxy": "proxy" in kinds,
        },
    }


# The broker owns the sign-in processes; see `services/oauthbroker`.
BROKER = os.environ.get("OAUTH_BROKER_URL", "http://oauthbroker:8100")
# Longer than the broker's own read waits, so a timeout surfaces as the broker's message.
BROKER_TIMEOUT = 75.0


def _broker(path: str, payload: dict) -> dict:
    """Call the broker, turning its failures into ones a user can act on."""
    try:
        response = httpx.post(f"{BROKER}{path}", json=payload, timeout=BROKER_TIMEOUT)
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Claude sign-in is unavailable right now") from exc
    if response.status_code >= 400:
        detail = response.json().get("detail", "the Claude sign-in failed")
        raise HTTPException(response.status_code, detail=detail)
    return response.json()


@router.post("/claude/oauth/start")
def claude_oauth_start(request: Request, *,
                       user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Begin a sign-in and hand back the URL to open."""
    rate_limit(request, f"oauth:{user_id}", limit=10, per_seconds=3600)
    body = _broker("/start", {"user_id": str(user_id)})
    audit(logger, "credentials.oauth_started", user=user_id)
    return {"session_id": body["session_id"], "url": body["url"]}


class OAuthFinishBody(BaseModel):
    session_id: str = Field(min_length=8, max_length=128)
    # The short code the browser shows, not the token.
    code: str = Field(min_length=4, max_length=512)


@router.post("/claude/oauth/finish")
def claude_oauth_finish(body: OAuthFinishBody, *,
                        store: SecretStore = Depends(get_store),
                        user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Exchange the code for a token, and store it only if it works."""
    token = _broker("/finish", {"user_id": str(user_id),
                                "session_id": body.session_id, "code": body.code})["token"]
    with redacting(token):
        result = validate_credential("claude_oauth", token)
        if not result.ok:
            audit(logger, "credentials.oauth_refused", user=user_id)
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                detail=("Claude signed you in, but the token it returned could not make a request: "
                        f"{result.detail} Nothing was saved. Please try connecting again."))
        store.put(user_id, "claude_oauth", token, validated=True)
    audit(logger, "credentials.oauth_stored", user=user_id, validated=True)
    return {"status": "saved", "validated": True, "detail": result.detail}


class OAuthCancelBody(BaseModel):
    session_id: str = Field(min_length=8, max_length=128)


@router.post("/claude/oauth/cancel")
def claude_oauth_cancel(body: OAuthCancelBody,
                        user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Back out. Cheap, and it frees the process instead of leaving it to the TTL."""
    _broker("/cancel", {"session_id": body.session_id})
    return {"status": "cancelled"}


class RevealBody(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


@router.post("/{kind}/reveal")
def reveal_credential(kind: str, body: RevealBody, request: Request, *,
                      db: Session = Depends(get_db),
                      store: SecretStore = Depends(get_store),
                      user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Hand back the plaintext, once, after the account password."""
    from libs.auth.passwords import verify_password  # noqa: PLC0415 - the one route that needs it

    if kind not in SECRET_KINDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"unknown credential {kind!r}")
    rate_limit(request, f"reveal:{user_id}", limit=10, per_seconds=3600)

    user = db.get(User, user_id)
    if not verify_password(user.password_hash, body.password):
        audit(logger, "credentials.reveal_refused", user=user_id, kind=kind)
        # Deliberately the same shape as a failed login: no oracle, no timing shortcut.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="that password is not correct")

    with store.use(user_id, kind) as secret:
        if secret is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="nothing stored")
        value = secret.reveal()

    audit(logger, "credentials.revealed", user=user_id, kind=kind)
    return {"value": value}


@router.delete("/{kind}")
def delete_credential(kind: str, db: Session = Depends(get_db),
                      store: SecretStore = Depends(get_store),
                      user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Revoke one credential."""
    if kind not in SECRET_KINDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"unknown credential {kind!r}")

    removed = store.delete(user_id, kind)
    if kind == "telegram_bot":
        user = db.get(User, user_id)
        user.telegram_status = "unconfigured"
        user.telegram_chat_id = None
    return {"status": "deleted" if removed else "not stored"}
