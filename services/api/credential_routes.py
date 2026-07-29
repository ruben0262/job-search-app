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
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from libs.credentials import telegram_chat_id, validate_credential
from libs.db.models import ProxyCredential, User
from libs.db.models.user import SECRET_KINDS
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
    return {
        "credentials": [
            {"kind": row.kind, "last_4": row.last_4,
             "validated": row.validated_at is not None,
             "updated_at": row.updated_at.isoformat() if row.updated_at else None}
            for row in stored],
        "requirements": {
            "claude": bool(kinds & set(CLAUDE_KINDS)),
            "telegram_bot": "telegram_bot" in kinds,
            "proxy": "proxy" in kinds,
        },
    }


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
