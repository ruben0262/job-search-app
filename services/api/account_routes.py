"""The GDPR surface: what we disclose, what the user agreed to, and erasure.

Three obligations the cleared legal review depends on (`ROADMAP.md` Phase 3 step 11), all of them
things the product must *do* rather than things a document must claim:

- **`GET /legal/privacy` names every processor.** Anthropic, Telegram, the proxy provider — what
  each receives, what for, and whose credential pays. Served from `libs.legal` so it cannot drift
  from the consent screen.
- **`POST /account/consent` before any document text reaches Anthropic.** Reshaped once the sandbox
  went away: files no longer leave this server, so the moment consent is owed is *process data*,
  not upload. `/process` refuses without it.
- **`DELETE /account` erases, and the erasure is provable.** With no Files API objects outside
  Postgres there is nothing to orphan, so the cascade is the whole story — and a test asserts no
  row referencing the user survives in any table.
"""
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
from libs.legal import DISCLOSURE_VERSION, disclosure
from libs.obs import audit
from libs.secrets import SecretStore
from services.api.auth_routes import _clear_session_cookies
from services.api.deps import current_user, get_db, get_store

logger = logging.getLogger(__name__)
router = APIRouter(tags=["account"])


@router.get("/legal/privacy")
def privacy() -> dict:
    """Every processor, named. Public — it is linked from signup, before anyone has an account.

    Naming them individually rather than saying "trusted third parties" is the entire requirement:
    a user deciding whether to upload their employment history is entitled to know it will be read
    by a US company, and which one.
    """
    return disclosure()


@router.get("/account/consent")
def consent_state(db: Session = Depends(get_db),
                  user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Whether this account has agreed to the **current** disclosure.

    Version-compared, not merely present. A policy that names a new processor is a new disclosure,
    and an old agreement is not an answer to it — so adding one re-asks everybody, which is the
    point rather than a cost to route around.
    """
    user = db.get(User, user_id)
    given = (user.processing_consent_at is not None
             and user.processing_consent_version == DISCLOSURE_VERSION)
    return {
        "granted": given,
        "granted_at": user.processing_consent_at.isoformat() if user.processing_consent_at else None,
        "granted_version": user.processing_consent_version,
        "current_version": DISCLOSURE_VERSION,
        "disclosure": disclosure(),
    }


class ConsentBody(BaseModel):
    # The client echoes back the version it displayed. If it does not match what the server would
    # serve, the user agreed to a screen that is no longer current and the answer is refused rather
    # than recorded against the wrong text.
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
    """Withdraw. Processing stops being available; nothing already produced is undone.

    Said plainly rather than implied: a profile already built stays built, and what Anthropic
    already processed cannot be recalled. Deleting the account is the stronger action and the one
    that removes what this platform holds.
    """
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
    """Change the password. The semantics are the reference implementation's
    (FlowSolutions `security/handler.go`, via `AUTH_REUSE.md`), because each was learned the
    hard way there:

    - **the current password is required** — a stolen session cookie must not be enough to lock
      the owner out of their own account;
    - **the new password may not equal the current one** — accepting it would record a change
      that changed nothing while still revoking every session;
    - **every session is revoked, including this one.** If the password is being changed because
      it leaked, a session the thief already holds must die with it. The client signs the user
      back in with the new password, which is one honest re-login rather than a silently
      privileged session surviving the rotation.
    """
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
    # The refresh sessions are dead in the database; the cookies still sitting in this browser are
    # what would otherwise keep presenting them. The access cookie dies here too rather than at its
    # natural expiry — sixty seconds of ghost session after "your password changed" reads as broken.
    _clear_session_cookies(response)
    audit(logger, "account.password_changed", user=user_id, sessions_revoked=revoked)
    return {"status": "changed", "force_relogin": True}


class DeleteBody(BaseModel):
    # Not a checkbox. Erasure is irreversible and unwinds an account somebody spent real money
    # building, so it asks for the one thing an attacker with a stolen session does not have.
    password: str = Field(min_length=1, max_length=1024)


@router.delete("/account")
def delete_account(body: DeleteBody, db: Session = Depends(get_db),
                   store: SecretStore = Depends(get_store),
                   user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Erase the account. Everything cascades from `users`, and the credentials are crypto-shredded.

    **Both, deliberately.** The cascade removes the rows; `purge` destroys the user's DEK, so any
    ciphertext that survives in a backup taken minutes ago is unreadable rather than merely deleted
    (`TENANT_SAFETY.md` §2). A backup restore that resurrected a stranger's Claude key would be the
    worst possible outcome of a delete request.
    """
    user = db.get(User, user_id)
    if not verify_password(user.password_hash, body.password):
        # Deliberately the same shape as a failed login: no oracle, no timing shortcut.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="that password is not correct")

    store.purge(user_id)
    db.delete(user)
    audit(logger, "account.deleted", user=user_id)
    return {"status": "deleted"}


class AccountPatch(BaseModel):
    # The one delivery knob that lives on the user row rather than in the search config. Bounds
    # mirror the DB constraint (`notify_interval_bounds`) so the error is a sentence rather than an
    # IntegrityError.
    notify_interval_min: int | None = Field(default=None, ge=15, le=1440)


@router.patch("/account")
def patch_account(body: AccountPatch, db: Session = Depends(get_db),
                  user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Settings the user may change directly. Only what is named is touched."""
    user = db.get(User, user_id)
    if body.notify_interval_min is not None:
        user.notify_interval_min = body.notify_interval_min
        audit(logger, "account.notify_interval", user=user_id, minutes=body.notify_interval_min)
    return {"status": "ok", "notify_interval_min": user.notify_interval_min}


@router.get("/account")
def account(db: Session = Depends(get_db), user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """What the settings page shows. Everything onboarding collected, minus anything secret."""
    user = db.get(User, user_id)
    return {
        "email": user.email,
        "full_name": user.full_name,
        "links": user.links or [],
        "timezone": user.timezone,
        "active": user.active,
        "notify_interval_min": user.notify_interval_min,
        "telegram_status": user.telegram_status,
        "plan": user.plan_id,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "consent_version": user.processing_consent_version,
    }


def has_consented(db: Session, user_id: _uuid.UUID) -> bool:
    """Used by `/process` to refuse before a single character reaches Anthropic."""
    user = db.get(User, user_id)
    return bool(user and user.processing_consent_at is not None
                and user.processing_consent_version == DISCLOSURE_VERSION)
