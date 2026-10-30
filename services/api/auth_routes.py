"""Signup, verification, login, refresh, logout."""
from __future__ import annotations

import logging
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from libs.auth import (
    MIN_PASSWORD_LENGTH,
    SessionExpired,
    consume_token,
    hash_password,
    issue_session,
    issue_token,
    needs_rehash,
    password_problems,
    revoke_all,
    revoke_session,
    rotate_session,
    token_owner,
    verify_password,
)
from libs.auth.access import issue_access_token
from libs.auth.sessions import ACCESS_TTL, REFRESH_TTL, session_owner
from libs.db import find_one, get, insert, now
from libs.db.models import User, UserSession
from libs.mail import EmailSender, MailError, reset_email, send_within_cap, verification_email
from libs.notify import site_url
from libs.secrets import redacting
from services.api.deps import get_db, get_mailer, rate_limit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
REFRESH_PATH = "/auth/refresh"

# The response every signup and every failed login gets, whatever actually happened.
OPAQUE_SIGNUP = {"status": "check your email"}
OPAQUE_LOGIN_ERROR = "email or password is incorrect"


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=256)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=256)


def _set_session_cookies(response: Response, access: str, refresh: str) -> None:
    response.set_cookie(
        ACCESS_COOKIE, access, httponly=True, secure=True, samesite="lax",
        max_age=int(ACCESS_TTL.total_seconds()), path="/")
    # Strict, and scoped to the one path that consumes it.
    response.set_cookie(
        REFRESH_COOKIE, refresh, httponly=True, secure=True, samesite="strict",
        max_age=int(REFRESH_TTL.total_seconds()), path=REFRESH_PATH)


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_PATH)


@router.post("/signup", status_code=status.HTTP_202_ACCEPTED)
def signup(body: SignupRequest, request: Request,
           db: Database = Depends(get_db),
           mailer: EmailSender = Depends(get_mailer)) -> dict:
    """Create an unverified account and email a link."""
    rate_limit(request, "signup", limit=5, per_seconds=3600)

    problems = password_problems(body.password, email=body.email)
    if problems:
        # The password rules are told to the caller: they are about the value they just chose.
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail={"password": problems})

    email = body.email.lower()
    try:
        user = insert(db, User(email=email, password_hash=hash_password(body.password),
                               status="pending_verification", active=False))
    except DuplicateKeyError:
        logger.info("signup for an existing address; answering opaquely")
        return OPAQUE_SIGNUP

    token = issue_token(db, user.id, "verify_email")
    _send_verification(db, mailer, email, token)
    return OPAQUE_SIGNUP


class AuthStatus(BaseModel):
    status: str


class ResendRequest(BaseModel):
    email: EmailStr


@router.post("/resend", status_code=status.HTTP_202_ACCEPTED)
def resend(body: ResendRequest, request: Request, db: Database = Depends(get_db),
           mailer: EmailSender = Depends(get_mailer)) -> AuthStatus:
    """Email a fresh verification link to an account still waiting for one. Answers the same anyway."""
    email = body.email.lower()
    rate_limit(request, f"resend:{email}", limit=3, per_seconds=3600)

    user = find_one(db, User, {"email": email})
    if user is not None and user.status == "pending_verification":
        # Issuing revokes the older links, so the newest one in the inbox is the one that works.
        _send_verification(db, mailer, email, issue_token(db, user.id, "verify_email"))
    else:
        logger.info("verification resend for an unknown or verified address; answering opaquely")
    return AuthStatus(**OPAQUE_SIGNUP)


class VerifyRequest(BaseModel):
    token: str = Field(min_length=1, max_length=256)


@router.post("/verify")
def verify(body: VerifyRequest, request: Request, response: Response,
           db: Database = Depends(get_db)) -> AuthStatus:
    """Redeem a verification link and sign in: opening it proves the inbox, as a password would."""
    user_id = consume_token(db, body.token, "verify_email")
    if user_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="this link is invalid or has expired — request another")

    user = get(db, User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="account no longer exists")
    # The same refusal login gives: a link must not become a way round a suspension.
    if user.status in ("suspended", "deleted"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="this account is not active")
    _verified(db, user)
    _sign_in(request, response, db, user)
    return AuthStatus(status="verified")


class ForgotRequest(BaseModel):
    email: EmailStr


@router.post("/forgot", status_code=status.HTTP_202_ACCEPTED)
def forgot(body: ForgotRequest, request: Request, db: Database = Depends(get_db),
           mailer: EmailSender = Depends(get_mailer)) -> dict:
    """Email a reset link."""
    rate_limit(request, f"forgot:{body.email.lower()}", limit=3, per_seconds=3600)

    user = find_one(db, User, {"email": body.email.lower()})
    if user is not None and user.status not in ("suspended", "deleted"):
        token = issue_token(db, user.id, "reset_password")
        url = site_url(f"/#/reset?token={token}")
        subject, mail_body = reset_email(url)
        with redacting(token):
            try:
                send_within_cap(db, mailer, user.email, subject, mail_body,
                                purpose="reset_password")
            except MailError:
                logger.warning("reset email failed; the user can ask again", exc_info=True)
    else:
        logger.info("password reset for an unknown or inactive address; answering opaquely")
    return {"status": "check your email"}


class ResetRequest(BaseModel):
    token: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


@router.post("/reset")
def reset(body: ResetRequest, request: Request, db: Database = Depends(get_db)) -> dict:
    """Redeem a reset link and set the new password."""
    rate_limit(request, "reset", limit=10, per_seconds=900)

    expired = HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="this link is invalid or has expired — request another")
    # Looked at, not spent, until the new password passes: a refused password keeps the link working.
    user = get(db, User, token_owner(db, body.token, "reset_password"))
    if user is None:
        raise expired

    problems = password_problems(body.new_password, email=user.email)
    if problems:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"password": problems})
    if consume_token(db, body.token, "reset_password") != user.id:
        raise expired

    db[User.COLLECTION].update_one({"_id": user.id}, {"$set": {
        "password_hash": hash_password(body.new_password), "updated_at": now()}})
    revoke_all(db, user.id)
    # Arriving here also proves control of the inbox, which is what verification proves.
    if user.email_verified_at is None:
        _verified(db, user)
    return {"status": "reset"}


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response,
          db: Database = Depends(get_db)) -> dict:
    """Exchange a password for a session. Sets both cookies."""
    rate_limit(request, f"login:{body.email.lower()}", limit=10, per_seconds=900)

    user = find_one(db, User, {"email": body.email.lower()})
    # The hash is verified even when the user does not exist, so timing reveals no addresses.
    stored = user.password_hash if user else _DUMMY_HASH
    if not verify_password(stored, body.password) or user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=OPAQUE_LOGIN_ERROR)

    if user.status == "pending_verification":
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="confirm your email address first")
    if user.status in ("suspended", "deleted"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="this account is not active")

    if needs_rehash(user.password_hash):
        # The parameters moved since this hash was written.
        db[User.COLLECTION].update_one({"_id": user.id},
                                       {"$set": {"password_hash": hash_password(body.password)}})

    _sign_in(request, response, db, user)
    return {"status": "ok"}


def _sign_in(request: Request, response: Response, db: Database, user: User) -> None:
    """A session and both cookies: what a correct password earns, and what a verification link does."""
    session = issue_session(db, user.id)
    _set_session_cookies(response, issue_access_token(user.id, session.session_id),
                         session.refresh_token)
    db[User.COLLECTION].update_one({"_id": user.id}, {"$set": {"last_active_at": now()}})


def _verified(db: Database, user: User) -> None:
    """The inbox is proven: the account is active, and verified from the first time that was shown."""
    db[User.COLLECTION].update_one({"_id": user.id}, {"$set": {
        "email_verified_at": user.email_verified_at or now(), "status": "active", "active": True,
        "updated_at": now()}})


# `response_model=None`: the handler returns either a dict or a Response.
@router.post("/refresh", response_model=None)
def refresh(request: Request, response: Response,
            db: Database = Depends(get_db)) -> dict | JSONResponse:
    """Rotate the refresh token and mint a new access token."""
    presented = request.cookies.get(REFRESH_COOKIE)
    if not presented:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="no session")

    try:
        rotated = rotate_session(db, presented)
    except SessionExpired as exc:
        # Returned, not raised.
        failure = JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED,
                               content={"detail": str(exc)})
        _clear_session_cookies(failure)
        return failure

    _set_session_cookies(response, issue_access_token(_owner(db, rotated.session_id), rotated.session_id),
                         rotated.refresh_token)
    return {"status": "ok"}


@router.post("/logout")
def logout(request: Request, response: Response, db: Database = Depends(get_db)) -> dict:
    """End this session. Always answers 200 — logging out twice is not an error."""
    presented = request.cookies.get(REFRESH_COOKIE)
    if presented:
        revoke_session(db, presented)
    _clear_session_cookies(response)
    return {"status": "ok"}


@router.post("/logout-everywhere")
def logout_everywhere(request: Request, response: Response,
                      db: Database = Depends(get_db)) -> dict:
    """End every session for this user. What a password change and a suspected theft both need."""
    presented = request.cookies.get(REFRESH_COOKIE)
    if not presented:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="no session")
    owner = session_owner(db, presented)
    if owner is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="no session")
    revoked = revoke_all(db, owner)
    _clear_session_cookies(response)
    return {"status": "ok", "sessions_ended": revoked}


def _send_verification(db: Database, mailer: EmailSender, email: str, token: str) -> None:
    """Send the link, and treat any delivery failure as recoverable."""
    # The SPA's hash route, not the API's path.
    url = site_url(f"/#/verify?token={token}")
    subject, body = verification_email(url)
    # The token grants access to the account, so it is scrubbed from anything logged in flight.
    with redacting(token):
        try:
            send_within_cap(db, mailer, email, subject, body, purpose="verify_email")
        except MailError:
            logger.warning("verification email to %s failed; the account still exists", email,
                           exc_info=True)


def _owner(db: Database, session_id: _uuid.UUID) -> _uuid.UUID:
    return get(db, UserSession, session_id).user_id


# A real argon2 hash of a value nobody has.
_DUMMY_HASH = hash_password("not-a-real-password-" + "x" * 24)
