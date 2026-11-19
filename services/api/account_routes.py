"""The GDPR surface: the legal pages, what a person agreed to, and erasure."""
from __future__ import annotations

import logging
import uuid as _uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from pymongo.database import Database

import libs.notify as notifylib
from libs.auth.passwords import hash_password, password_problems, verify_password
from libs.auth.sessions import revoke_all
from libs.db import delete_account as erase_account
from libs.db import get, now
from libs.db.models import User
from libs.legal.documents import render
from libs.obs import audit
from services.api.auth_routes import _clear_session_cookies
from services.api.billing_routes import _stripe_answers, end_membership
from services.api.deps import current_user, get_db

logger = logging.getLogger(__name__)
router = APIRouter(tags=["account"])


class LegalDocument(BaseModel):
    name: str
    title: str
    # The version the sign-up box agrees to (`libs.legal.LEGAL_VERSION`), the same on all four.
    version: str
    markdown: str


@router.get("/legal/{name}")
def legal(name: Literal["terms", "privacy", "cookies", "data"],
          db: Database = Depends(get_db)) -> LegalDocument:
    """One of the four legal pages, filled in (`libs.legal.documents`). Public: sign-up links them before
    anybody has an account."""
    return LegalDocument(**render(db, name))


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


@router.put("/account/password")
def change_password(body: PasswordChange, response: Response, db: Database = Depends(get_db),
                    user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Change the password."""
    user = get(db, User, user_id)
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

    _set(db, user_id, password_hash=hash_password(body.new_password))
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
def delete_account(body: DeleteBody, db: Database = Depends(get_db),
                   user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Erase the account."""
    user = get(db, User, user_id)
    if not verify_password(user.password_hash, body.password):
        # Deliberately the same shape as a failed login: no oracle, no timing shortcut.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="that password is not correct")

    # Their membership first: an account deleted while Stripe still bills it is a charge nobody can stop.
    with _stripe_answers("end the membership of an account being deleted"):
        end_membership(user)
    # Their credentials and the key that could read them go with the account (`libs.db.delete_account`).
    erase_account(db, user_id)
    audit(logger, "account.deleted", user=user_id)
    return {"status": "deleted"}


def _set(db: Database, user_id: _uuid.UUID, **fields) -> None:
    db[User.COLLECTION].update_one({"_id": user_id}, {"$set": {**fields, "updated_at": now()}})


class AccountOut(BaseModel):
    email: str
    telegram_status: str


@router.get("/account")
def account(db: Database = Depends(get_db),
            user_id: _uuid.UUID = Depends(current_user)) -> AccountOut:
    """What the settings page shows, minus anything secret. Their name and links are on
    `/onboarding/identity`, their words on `/onboarding/review`."""
    user = get(db, User, user_id)
    return AccountOut(email=user.email, telegram_status=user.telegram_status)
