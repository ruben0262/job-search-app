"""Storing, checking, and revoking the credentials a user brings.

Every one of these is a live credential belonging to a stranger, so three rules hold on every route:

- **Write-only.** No response ever contains a credential value. The list endpoint returns `last_4`
  and timestamps, which is what `TenantRepository.secret_metadata` selects and all it can select.
- **Validated at save.** A real minimal call proves the credential works before it is accepted
  (`TENANT_SAFETY.md` §2). A key that fails here fails at a form field, which is the cheapest
  possible moment.
- **Never logged.** The value is registered with the redaction filter for the window it is in
  flight, which covers the traceback an HTTP client raises on a failed validation — the one place a
  credential most often escapes.

Onboarding requires a Claude credential of *either* kind. `claude_api` and `claude_oauth` are two
adapters behind one requirement (decision D2), and which one a user has depends on whether they buy
API credit or hold a subscription.
"""
from __future__ import annotations

import logging
import os
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
from libs.obs import audit
from libs.secrets import SecretStore, redacting
from services.api.deps import current_user, get_db, get_store, rate_limit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/credentials", tags=["credentials"])

# Either satisfies the Claude requirement. Onboarding cannot complete without one of them.
CLAUDE_KINDS = ("claude_api", "claude_oauth")


class CredentialBody(BaseModel):
    value: str = Field(min_length=8, max_length=4096)
    # A credential the platform could not reach the provider to check is stored unvalidated rather
    # than refused — an outage on their side must not stop a user finishing onboarding. The account
    # page shows which credentials are unvalidated, and the workers re-check on first use.
    allow_unvalidated: bool = False


@router.put("/{kind}")
def put_credential(kind: str, body: CredentialBody, request: Request, *,
                   db: Session = Depends(get_db),
                   store: SecretStore = Depends(get_store),
                   user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Store or replace one credential. Replaces in place; the previous value is gone.

    Rotation overwrites rather than versions, because keeping history here would mean keeping a
    revoked credential decryptable.
    """
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
            # **The credential row, not just the secret.** `user_secrets` holds the URL; the lease
            # every proxied fetch has to acquire is granted from `proxy_credentials`, which carries
            # the daily cap and the health status. Saving one without the other stored a working
            # credential that nothing could ever use — which is what had happened: a proxy secret on
            # the account, no row beside it, and the LinkedIn tier silently skipping the user
            # forever because `acquire_lease` had nothing to grant from.
            _ensure_proxy_credential(db, user_id)

        if kind == "telegram_bot" and result.ok:
            # The chat id is read for them rather than asked for. It is the step that confuses
            # people more than the rest of the setup put together (`TENANT_SAFETY.md` §2).
            chat_id = telegram_chat_id(value)
            user = db.get(User, user_id)
            if chat_id:
                user.telegram_chat_id = chat_id
                user.telegram_status = "connected"
                extra["chat_id_captured"] = "yes"
            else:
                # Not an error: they have saved a valid token and simply not pressed Start yet.
                user.telegram_status = "unconfigured"
                extra["chat_id_captured"] = "no"

    return {"status": "saved", "validated": result.ok,
            "detail": result.detail, **extra}


def _ensure_proxy_credential(db: Session, user_id) -> None:
    """Give this user a proxy credential row if they have none. Idempotent.

    The cap and status are left at their defaults on an existing row: re-saving a URL is a rotation,
    not a reset, and silently returning a quarantined credential to `healthy` would put a user
    straight back into whatever got them quarantined.
    """
    if db.get(ProxyCredential, user_id) is not None:
        return
    db.add(ProxyCredential(user_id=user_id, secret_ref=f"{user_id}:proxy"))
    db.flush()


@router.post("/telegram/connect")
def telegram_connect(db: Session = Depends(get_db),
                     store: SecretStore = Depends(get_store),
                     user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Re-read the chat id after the user has pressed Start. Idempotent.

    Separate from saving the token because the two happen minutes apart: the token is pasted, then
    the user goes to their phone, then they come back. Asking them to re-paste the token to finish
    connecting would be gratuitous.
    """
    with store.use(user_id, "telegram_bot") as token:
        if token is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                detail="save your bot token first")
        chat_id = telegram_chat_id(token.reveal())

    user = db.get(User, user_id)
    if not chat_id:
        return {"status": "waiting",
                "detail": "press Start in your bot, then try again"}
    user.telegram_chat_id = chat_id
    user.telegram_status = "connected"
    return {"status": "connected"}


class ChatBody(BaseModel):
    # A personal chat id is a positive integer. Negative ones are groups and channels, and pasting
    # the channel id here is the mistake worth catching by shape rather than by a failed send.
    chat_id: str = Field(min_length=4, max_length=32, pattern=r"^\d{4,}$")


@router.put("/telegram/chat")
def set_chat(body: ChatBody, db: Session = Depends(get_db),
             store: SecretStore = Depends(get_store),
             user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Set the chat id by hand, proven by messaging it.

    **The way through when auto-capture cannot work.** `telegram/connect` reads whoever last
    messaged the bot, which depends on the update still being queued at Telegram and on nothing
    else having consumed it. Neither is something the platform controls, and when it comes back
    empty there was nothing else to try: the page could not complete, so onboarding stopped dead at
    a step whose only instruction was to press Start again.

    A chat id is not a secret and not guessable-into-harm: writing somebody else's here only means
    the bot cannot message them, which the send below catches immediately.
    """
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
    """Point the job stream at a channel. Proven by posting to it, not by trusting the ID.

    On success every route goes to the channel and the private chat keeps commands and documents —
    the reference's split. One channel rather than the per-stream routing the table supports,
    because that is the setup being asked for; the routes stay per-key underneath for the day
    somebody wants the split back.
    """
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
    """What the account page shows: which credentials exist, never what they are.

    Also reports whether onboarding's credential requirement is met, so the front end does not have
    to re-derive the rule from the list and get it subtly wrong.
    """
    from libs.tenant.repository import TenantRepository  # noqa: PLC0415 - avoids an import cycle

    stored = TenantRepository(db).secret_metadata(user_id)
    kinds = {row.kind for row in stored}
    user = db.get(User, user_id)

    # Where the jobs actually go. Every route is set to the same channel by `set_channel`, so the
    # first value is the channel; `None` means the private bot chat is still the destination.
    routes = user.telegram_routes or {}
    channel_id = next(iter(routes.values()), None) if routes else None

    return {
        "credentials": [
            {"kind": row.kind, "last_4": row.last_4,
             "validated": row.validated_at is not None,
             "updated_at": row.updated_at.isoformat() if row.updated_at else None}
            for row in stored],
        # **The IDs are shown, not hidden.** A chat id and a channel id are addresses, not secrets —
        # they are useless without the bot token, which is never returned here. Withholding them
        # only stopped a user confirming that the thing they configured is the thing we will send
        # to, which is the one question this page exists to answer.
        "telegram": {
            "status": user.telegram_status,
            "chat_id": user.telegram_chat_id,
            "channel_id": channel_id,
            "destination": "channel" if channel_id else ("chat" if user.telegram_chat_id else None),
        },
        "requirements": {
            "claude": bool(kinds & set(CLAUDE_KINDS)),
            "telegram_bot": "telegram_bot" in kinds,
            # **The channel is required, not a nicety.** Delivery is the product — an account with
            # no route has a working pipeline and nowhere to put its output — and the private chat
            # is a fallback for commands, not a feed anyone scrolls.
            "telegram_channel": channel_id is not None,
            "proxy": "proxy" in kinds,
        },
    }


# The broker owns the sign-in processes; see `services/oauthbroker`. Reached on the compose
# network only, which is why these calls carry no credential of their own.
BROKER = os.environ.get("OAUTH_BROKER_URL", "http://oauthbroker:8100")
# Longer than the broker's own read waits, so a broker timeout surfaces as the broker's message
# rather than as a client timeout that says nothing useful.
BROKER_TIMEOUT = 75.0


def _broker(path: str, payload: dict) -> dict:
    """Call the broker, turning its failures into ones a user can act on.

    A transport error here means the broker is down or missing the Claude CLI. That is a service
    problem and says so; it must not surface as a 500, because the user would retry forever.
    """
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
    # The short code the browser shows, not the token. Whitespace is stripped downstream because
    # it is copied by hand and a trailing space is the most common way this fails.
    code: str = Field(min_length=4, max_length=512)


@router.post("/claude/oauth/finish")
def claude_oauth_finish(body: OAuthFinishBody, *,
                        store: SecretStore = Depends(get_store),
                        user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Exchange the code for a token, and store it **only if it works**.

    The token goes through `validate_credential` like a pasted one: the CLI minting it proves the
    browser flow completed, not that the credential can reach the model, and those come apart on an
    account with no active subscription.

    **Unlike a pasted credential, a failure here is never stored.** Everywhere else the platform
    keeps an unvalidated value, because the user typed something they believe is right and a
    provider outage must not block them. Nothing about that applies to a token this service minted
    itself: there is no typo to preserve, and if it does not authenticate then either Anthropic
    turned it down or we mishandled it on the way out of the CLI. Both are ours to fix.

    Storing it anyway is what hid a real bug (2026-08-01). A rendering fault dropped one character
    from the token, and the flow reported success while the account held a credential that could not
    make a single request -- indistinguishable, on the page, from a temporary API blip.

    A previously stored credential survives, because nothing is written on this path unless the new
    one is proven. Re-connecting with a subscription that has lapsed cannot cost somebody the
    working token they already had.
    """
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
    """Hand back the plaintext, once, after the account password.

    **The exception to write-only, and the shape of the exception is the point** (owner's decision,
    2026-07-30). A copy button cannot exist without the browser receiving the plaintext — there is
    no cryptographic trick that yields a usable token without revealing it. So the security lives
    in the gate instead: the password is required (a stolen session cookie is not enough), the
    value is returned in this one response and never listed anywhere, the attempt is audited either
    way, and the rate limit keeps a stolen password from becoming a quiet bulk export.
    """
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
    """Revoke one credential. Removing the Telegram token also disconnects delivery.

    Leaving `telegram_status` as `connected` with no token would queue notify jobs that can only
    fail — and each one would look like a delivery problem rather than a missing credential.
    """
    if kind not in SECRET_KINDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"unknown credential {kind!r}")

    removed = store.delete(user_id, kind)
    if kind == "telegram_bot":
        user = db.get(User, user_id)
        user.telegram_status = "unconfigured"
        user.telegram_chat_id = None
    return {"status": "deleted" if removed else "not stored"}
