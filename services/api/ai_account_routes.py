"""The AI accounts a person links in Settings (owner, 2026-11-09): sign-ins to their own Claude, ChatGPT
(Codex) or Google (Gemini) account, and API keys. Several, one in use, never shown back
(`libs.llm.accounts`). Without one in use, no CV or cover letter is made.

A sign-in runs in the broker (`services/oauthbroker`), which hands the finished login here to be sealed.
"""
from __future__ import annotations

import logging
import os
import uuid as _uuid
from datetime import datetime
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from pymongo.database import Database

from libs import settings
from libs.credentials import validate_credential
from libs.llm import accounts
from libs.obs import audit
from libs.secrets import SecretStore, redacting
from services.api.deps import current_user, get_db, get_store, rate_limit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ai-accounts", tags=["ai accounts"])

LoginKind = Literal["claude_login", "codex_login", "gemini_login"]
KeyKind = Literal["claude_api", "openai_api", "gemini_api"]

# The broker owns the sign-in processes; see `services/oauthbroker`.
BROKER = os.environ.get("OAUTH_BROKER_URL", "http://oauthbroker:8100")
# Longer than the broker's own waits, so a timeout surfaces as the broker's message.
BROKER_TIMEOUT = 75.0


class AiAccount(BaseModel):
    id: str
    kind: Literal["claude_login", "codex_login", "gemini_login", "claude_api", "openai_api", "gemini_api"]
    # "Claude Max · name@example.com", "OpenAI API key": nothing of the secret.
    label: str
    # The one their CVs and cover letters are written with.
    in_use: bool
    linked_at: datetime
    # The model it writes with, the one the platform picked for its provider (owner, 2026-11-10: one model
    # each, chosen in Admin, never by the person); None where the tool picks from their plan (ChatGPT).
    model: str | None


class AiAccounts(BaseModel):
    accounts: list[AiAccount]


def _listing(db: Database, user_id: _uuid.UUID) -> AiAccounts:
    # What the docgen worker asks (`settings.document_model`), so the page cannot name another model.
    chosen = settings.snapshot(db)
    return AiAccounts(accounts=[
        AiAccount(id=each.id, kind=each.kind, label=each.label, in_use=each.in_use, linked_at=each.created_at,
                  model=settings.document_model(chosen, accounts.PROVIDER[each.kind]) or None)
        for each in accounts.listing(db, user_id)])


@router.get("")
def list_accounts(db: Database = Depends(get_db),
                  user_id: _uuid.UUID = Depends(current_user)) -> AiAccounts:
    """Their AI accounts, newest first."""
    return _listing(db, user_id)


class KeyBody(BaseModel):
    kind: KeyKind
    value: str = Field(min_length=8, max_length=4096)
    # A key the provider could not be reached to check is kept unchecked, not refused: an outage must
    # not stop someone linking the key they know is right.
    allow_unvalidated: bool = False


@router.post("/keys")
def add_key(body: KeyBody, request: Request, *, db: Database = Depends(get_db),
            store: SecretStore = Depends(get_store),
            user_id: _uuid.UUID = Depends(current_user)) -> AiAccounts:
    """Link an API key, once the provider has said it works. It goes in use at once."""
    rate_limit(request, f"credential:{user_id}", limit=20, per_seconds=3600)
    value = body.value.strip()
    with redacting(value):
        result = validate_credential(body.kind, value)
        if not result.ok and not body.allow_unvalidated:
            # 422: the request was well-formed, the key was not.
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=result.detail)
        account = store.add(user_id, body.kind, value, label=accounts.KIND_NAMES[body.kind],
                            validated=result.ok)
    accounts.use(db, user_id, str(account))
    audit(logger, "ai_accounts.key_linked", user=user_id, kind=body.kind)
    return _listing(db, user_id)


def _broker(path: str, payload: dict) -> dict:
    """Call the broker, turning its failures into ones a person can act on."""
    try:
        response = httpx.post(f"{BROKER}{path}", json=payload, timeout=BROKER_TIMEOUT)
    except httpx.HTTPError as exc:
        logger.error("ai_accounts: the sign-in broker could not be reached (%s)", type(exc).__name__)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Signing in is unavailable right now. Please try again in a minute."
                            ) from exc
    if response.status_code >= 400:
        raise HTTPException(response.status_code, detail=response.json().get("detail", "the sign-in failed"))
    return response.json()


class SignInCancel(BaseModel):
    session_id: str = Field(min_length=8, max_length=128)


@router.post("/signin/cancel")
def cancel_signin(body: SignInCancel, user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Back out. Cheap, and it ends the tool rather than leaving it to the TTL. Declared before
    `/signin/{kind}`, which would otherwise take "cancel" for a kind."""
    del user_id
    _broker("/cancel", {"session_id": body.session_id})
    return {"status": "cancelled"}


class SignInStarted(BaseModel):
    session_id: str
    # Where the person signs in.
    url: str
    # Codex's one-time code, entered on OpenAI's page; none where a code is pasted back here.
    code: str | None = None


@router.post("/signin/{kind}")
def start_signin(kind: LoginKind, request: Request, *,
                 user_id: _uuid.UUID = Depends(current_user)) -> SignInStarted:
    """Begin a sign-in to their own account, and hand back the page to open."""
    rate_limit(request, f"oauth:{user_id}", limit=10, per_seconds=3600)
    body = _broker("/start", {"user_id": str(user_id), "kind": kind})
    return SignInStarted(session_id=body["session_id"], url=body["url"], code=body.get("code"))


class SignInFinish(BaseModel):
    session_id: str = Field(min_length=8, max_length=128)
    # The code Claude's or Google's page shows. None for Codex, whose code is entered on OpenAI's page.
    code: str | None = Field(default=None, min_length=4, max_length=512)


class SignInResult(BaseModel):
    # "waiting": Codex has not been told the code on OpenAI's page yet; the page asks again.
    status: Literal["done", "waiting"]
    accounts: AiAccounts | None = None


@router.post("/signin/{kind}/finish")
def finish_signin(kind: LoginKind, body: SignInFinish, *, db: Database = Depends(get_db),
                  store: SecretStore = Depends(get_store),
                  user_id: _uuid.UUID = Depends(current_user)) -> SignInResult:
    """Finish a sign-in, and seal the account it leaves. It goes in use at once."""
    answer = _broker("/finish", {"user_id": str(user_id), "session_id": body.session_id, "code": body.code})
    if answer["status"] == "waiting":
        return SignInResult(status="waiting")
    with redacting(answer["secret"]):
        account = store.add(user_id, kind, answer["secret"], label=answer["label"], validated=True)
    accounts.use(db, user_id, str(account))
    audit(logger, "ai_accounts.signed_in", user=user_id, kind=kind)
    return SignInResult(status="done", accounts=_listing(db, user_id))


class InUse(BaseModel):
    in_use: bool


@router.put("/{account_id}")
def switch(account_id: str, body: InUse, db: Database = Depends(get_db),
           user_id: _uuid.UUID = Depends(current_user)) -> AiAccounts:
    """Put an account in use, which takes every other one out of it, or take it out of use."""
    done = accounts.use(db, user_id, account_id) if body.in_use else accounts.stop(db, user_id, account_id)
    if not done:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="that account is not linked here")
    audit(logger, "ai_accounts.switched", user=user_id, account=account_id, in_use=body.in_use)
    return _listing(db, user_id)


@router.delete("/{account_id}")
def unlink(account_id: str, db: Database = Depends(get_db),
           user_id: _uuid.UUID = Depends(current_user)) -> AiAccounts:
    """Forget an account and its secret."""
    if not accounts.unlink(db, user_id, account_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="that account is not linked here")
    audit(logger, "ai_accounts.unlinked", user=user_id, account=account_id)
    return _listing(db, user_id)
