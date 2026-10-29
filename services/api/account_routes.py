"""The GDPR surface: what we disclose, what the user agreed to, and erasure."""
from __future__ import annotations

import logging
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

import libs.notify as notifylib
from libs.auth.passwords import hash_password, password_problems, verify_password
from libs.auth.sessions import revoke_all
from libs.db.models import User
from libs.legal import DISCLOSURE_VERSION, disclosure, has_consented
from libs.obs import audit
from libs.secrets import SecretStore
from services.api.auth_routes import _clear_session_cookies
from services.api.deps import current_user, get_db, get_store

logger = logging.getLogger(__name__)
router = APIRouter(tags=["account"])


class DisclosedProcessor(BaseModel):
    name: str
    purpose: str
    data: str
    on_your_credential: bool
    location: str


class Disclosure(BaseModel):
    version: str
    headline: str
    body: str
    processors: list[DisclosedProcessor]


@router.get("/legal/privacy", response_model=Disclosure)
def privacy() -> dict:
    """Every processor, named."""
    return disclosure()


class ConsentState(BaseModel):
    granted: bool
    granted_at: str | None
    granted_version: str | None
    current_version: str
    disclosure: Disclosure


@router.get("/account/consent", response_model=ConsentState)
def consent_state(db: Session = Depends(get_db),
                  user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Whether this account has agreed to the current disclosure."""
    user = db.get(User, user_id)
    return {
        "granted": has_consented(db, user_id),
        "granted_at": user.processing_consent_at.isoformat() if user.processing_consent_at else None,
        "granted_version": user.processing_consent_version,
        "current_version": DISCLOSURE_VERSION,
        "disclosure": disclosure(),
    }


class ConsentBody(BaseModel):
    # The client echoes back the version it displayed.
    version: str = Field(min_length=4, max_length=16)


@router.post("/account/consent")
def grant_consent(body: ConsentBody, db: Session = Depends(get_db),
                  user_id: _uuid.UUID = Depends(current_user)) -> dict:
    if body.version != DISCLOSURE_VERSION:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="the disclosure has changed since this was shown to you — please read it again")

    user = db.get(User, user_id)
    user.processing_consent_at = func.now()
    user.processing_consent_version = DISCLOSURE_VERSION
    audit(logger, "consent.granted", user=user_id, version=DISCLOSURE_VERSION)
    return {"status": "granted", "version": DISCLOSURE_VERSION}


@router.delete("/account/consent")
def withdraw_consent(db: Session = Depends(get_db),
                     user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Withdraw."""
    user = db.get(User, user_id)
    user.processing_consent_at = None
    user.processing_consent_version = None
    audit(logger, "consent.withdrawn", user=user_id)
    return {"status": "withdrawn",
            "detail": ("Processing is now unavailable. Your existing profile is unchanged — delete "
                       "your account if you want everything removed.")}


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


@router.put("/account/password")
def change_password(body: PasswordChange, response: Response, db: Session = Depends(get_db),
                    user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Change the password."""
    user = db.get(User, user_id)
    if not verify_password(user.password_hash, body.current_password):
        # Deliberately the same shape as a failed login: no oracle, no timing shortcut.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="that password is not correct")
    if body.new_password == body.current_password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="the new password must be different from the current one")
    problems = password_problems(body.new_password, email=user.email)
    if problems:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="that password " + "; ".join(problems))

    user.password_hash = hash_password(body.new_password)
    notifylib.notify(db, user_id, notifylib.PASSWORD_CHANGED,
                     "Every session was signed out. If this was not you, reset your password "
                     "from the sign-in page immediately.")
    revoked = revoke_all(db, user_id)
    # The refresh sessions are dead; the cookies in this browser would keep presenting them.
    _clear_session_cookies(response)
    audit(logger, "account.password_changed", user=user_id, sessions_revoked=revoked)
    return {"status": "changed", "force_relogin": True}


class DeleteBody(BaseModel):
    # Not a checkbox.
    password: str = Field(min_length=1, max_length=1024)


@router.delete("/account")
def delete_account(body: DeleteBody, db: Session = Depends(get_db),
                   store: SecretStore = Depends(get_store),
                   user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Erase the account."""
    user = db.get(User, user_id)
    if not verify_password(user.password_hash, body.password):
        # Deliberately the same shape as a failed login: no oracle, no timing shortcut.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="that password is not correct")

    store.purge(user_id)
    db.delete(user)
    audit(logger, "account.deleted", user=user_id)
    return {"status": "deleted"}


class AccountPatch(BaseModel):
    # How often the feed is sent is no longer here.
    active: bool | None = None


@router.patch("/account")
def patch_account(body: AccountPatch, db: Session = Depends(get_db),
                  user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Settings the user may change directly. Only what is named is touched."""
    user = db.get(User, user_id)
    if body.active is not None:
        user.active = body.active
        audit(logger, "account.delivery_toggled", user=user_id, active=body.active)
    return {"status": "ok", "active": user.active}


class AccountOut(BaseModel):
    email: str
    # Whether alerts are sent at all: the person's pause.
    active: bool
    telegram_status: str
    consent_version: str | None


@router.get("/account")
def account(db: Session = Depends(get_db),
            user_id: _uuid.UUID = Depends(current_user)) -> AccountOut:
    """What the settings page shows, minus anything secret. Their name and links are on
    `/onboarding/identity`, their words on `/onboarding/review`."""
    user = db.get(User, user_id)
    return AccountOut(email=user.email, active=user.active, telegram_status=user.telegram_status,
                      consent_version=user.processing_consent_version)
