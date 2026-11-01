"""Storing, checking, and revoking the credentials a user brings."""
from __future__ import annotations

import logging
import os
import uuid as _uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from pymongo.database import Database

from libs.credentials import validate_credential
from libs.db.models.user import CREDENTIAL_KINDS
from libs.obs import audit
from libs.secrets import SecretStore, redacting
from services.api.deps import current_user, get_db, get_store, rate_limit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/credentials", tags=["credentials"])



class CredentialBody(BaseModel):
    value: str = Field(min_length=8, max_length=4096)
    # A credential we could not reach the provider to check is stored unvalidated, not refused.
    allow_unvalidated: bool = False


class Saved(BaseModel):
    status: str
    validated: bool
    detail: str


@router.put("/{kind}")
def put_credential(kind: str, body: CredentialBody, request: Request, *,
                   db: Database = Depends(get_db),
                   store: SecretStore = Depends(get_store),
                   user_id: _uuid.UUID = Depends(current_user)) -> Saved:
    """Store or replace one credential."""
    if kind not in CREDENTIAL_KINDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail=f"unknown credential {kind!r}")
    rate_limit(request, f"credential:{user_id}", limit=20, per_seconds=3600)

    value = body.value.strip()
    with redacting(value):
        result = validate_credential(kind, value)
        if not result.ok and not body.allow_unvalidated:
            # 422 rather than 400: the request was well-formed, the credential was not.
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=result.detail)

        store.put(user_id, kind, value, validated=result.ok)

    return Saved(status="saved", validated=result.ok, detail=result.detail)


class StoredCredential(BaseModel):
    kind: str
    last_4: str | None
    validated: bool
    updated_at: str | None


class CredentialList(BaseModel):
    credentials: list[StoredCredential]


@router.get("")
def list_credentials(db: Database = Depends(get_db),
                     user_id: _uuid.UUID = Depends(current_user)) -> CredentialList:
    """What the account page shows: which credentials exist, never what they are."""
    from libs.tenant.repository import TenantRepository  # noqa: PLC0415 - avoids an import cycle

    stored = TenantRepository(db).secret_metadata(user_id)
    return CredentialList(
        credentials=[
            StoredCredential(kind=row.kind, last_4=row.last_4,
                             validated=row.validated_at is not None,
                             updated_at=row.updated_at.isoformat() if row.updated_at else None)
            for row in stored if row.kind in CREDENTIAL_KINDS])


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


class OAuthStarted(BaseModel):
    session_id: str
    # Where the person signs in to Claude.
    url: str


@router.post("/claude/oauth/start")
def claude_oauth_start(request: Request, *,
                       user_id: _uuid.UUID = Depends(current_user)) -> OAuthStarted:
    """Begin a sign-in and hand back the URL to open."""
    rate_limit(request, f"oauth:{user_id}", limit=10, per_seconds=3600)
    body = _broker("/start", {"user_id": str(user_id)})
    audit(logger, "credentials.oauth_started", user=user_id)
    return OAuthStarted(session_id=body["session_id"], url=body["url"])


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


class Removed(BaseModel):
    status: str


@router.delete("/{kind}")
def delete_credential(kind: str, store: SecretStore = Depends(get_store),
                      user_id: _uuid.UUID = Depends(current_user)) -> Removed:
    """Revoke one credential. A Telegram bot is not one of these: it is under `/telegram/bot`."""
    if kind not in CREDENTIAL_KINDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"unknown credential {kind!r}")
    return Removed(status="deleted" if store.delete(user_id, kind) else "not stored")
