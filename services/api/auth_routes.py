"""Signup, verification, login, refresh, logout.

The cookie policy is `AUTH_REUSE.md` §3, copied verbatim because each line of it is a decision
already made correctly in production:

- `HttpOnly` and `Secure` on both cookies, always. Not conditional on the environment — a
  development exception is how the production path ends up untested.
- Access cookie `SameSite=Lax`, refresh cookie `SameSite=Strict`.
- **The refresh cookie is scoped to the refresh path**, not `/`. This is the detail most
  implementations miss: it keeps the long-lived credential off every ordinary request, so an XSS on
  any other route cannot reach it.

Two behaviours worth stating because they look like bugs and are not:

- **Login and signup answer the same way for an unknown address as for a known one.** Differing
  responses turn the endpoint into an account-existence oracle (`TENANT_SAFETY.md` §5).
- **A failed verification email does not fail the signup.** The account exists and the user can ask
  for another link; making registration depend on Gmail being up would be worse.
"""
from __future__ import annotations

import logging
import os
import uuid as _uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

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
    verify_password,
)
from libs.auth.access import issue_access_token
from libs.auth.sessions import ACCESS_TTL, REFRESH_TTL, session_owner
from libs.db.models import User, UserSession
from libs.mail import EmailSender, MailError, reset_email, send_within_cap, verification_email
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


def _base_url() -> str:
    return os.environ.get("PUBLIC_URL", "http://localhost:8000").rstrip("/")


def _set_session_cookies(response: Response, access: str, refresh: str) -> None:
    response.set_cookie(
        ACCESS_COOKIE, access, httponly=True, secure=True, samesite="lax",
        max_age=int(ACCESS_TTL.total_seconds()), path="/")
    # Strict, and scoped to the one path that consumes it. An XSS anywhere else cannot read it and
    # an ordinary request never carries it.
    response.set_cookie(
        REFRESH_COOKIE, refresh, httponly=True, secure=True, samesite="strict",
        max_age=int(REFRESH_TTL.total_seconds()), path=REFRESH_PATH)


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_PATH)


@router.post("/signup", status_code=status.HTTP_202_ACCEPTED)
def signup(body: SignupRequest, request: Request,
           db: Session = Depends(get_db),
           mailer: EmailSender = Depends(get_mailer)) -> dict:
    """Create an unverified account and email a link. Always answers the same way.

    Invite-only at launch (D5), so an existing address is not an error the caller gets to see — it
    would confirm that the address has an account here.
    """
    rate_limit(request, "signup", limit=5, per_seconds=3600)

    problems = password_problems(body.password, email=body.email)
    if problems:
        # The password rules *are* told to the caller: they are about the value they just chose, not
        # about anyone else's account.
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail={"password": problems})

    email = body.email.lower()
    existing = db.scalar(select(User).where(User.email == email))
    if existing is not None:
        logger.info("signup for an existing address; answering opaquely")
        return OPAQUE_SIGNUP

    user = User(email=email, password_hash=hash_password(body.password),
                status="pending_verification", active=False)
    db.add(user)
    db.flush()

    token = issue_token(db, user.id, "verify_email")
    _send_verification(db, mailer, email, token)
    return OPAQUE_SIGNUP


class VerifyRequest(BaseModel):
    token: str = Field(min_length=1, max_length=256)


@router.post("/verify")
def verify(body: VerifyRequest, db: Session = Depends(get_db)) -> dict:
    """Redeem a verification link. Activates the account.

    Nothing here logs the user in. Verification proves control of the address; a session should
    still start from a deliberate login, so a forwarded link cannot hand someone a session.

    The token arrives in the body, matching what the SPA sends. It was a query parameter, which
    the page never sent — so every human verification failed 422 while the tests, which used the
    query form, stayed green. The e2e browser run is what caught it.
    """
    user_id = consume_token(db, body.token, "verify_email")
    if user_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="this link is invalid or has expired — request another")

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="account no longer exists")
    if user.email_verified_at is None:
        user.email_verified_at = _now()
    user.status = "active"
    user.active = True
    return {"status": "verified"}


class ForgotRequest(BaseModel):
    email: EmailStr


@router.post("/forgot", status_code=status.HTTP_202_ACCEPTED)
def forgot(body: ForgotRequest, request: Request, db: Session = Depends(get_db),
           mailer: EmailSender = Depends(get_mailer)) -> dict:
    """Email a reset link. Always answers the same way, whatever actually happened.

    The other half of a flow whose libs half (`reset_password` tokens, the mail template) shipped
    in Phase 3 with nothing calling it — until now a forgotten password was a locked account.
    Answering opaquely for an unknown address is the same no-oracle rule login follows; issuing a
    fresh token revokes the previous link (`issue_token` does), so the newest email is always the
    one that counts.
    """
    rate_limit(request, f"forgot:{body.email.lower()}", limit=3, per_seconds=3600)

    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user is not None and user.status not in ("suspended", "deleted"):
        token = issue_token(db, user.id, "reset_password")
        url = f"{_base_url()}/#/reset?token={token}"
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
def reset(body: ResetRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    """Redeem a reset link and set the new password.

    Every session dies with it — the reason a stranger resets somebody's password is that they are
    already inside, or the owner fears they are. Nothing here logs the user in either: proving
    control of an inbox is not a login, same as verification.
    """
    rate_limit(request, "reset", limit=10, per_seconds=900)

    user_id = consume_token(db, body.token, "reset_password")
    if user_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="this link is invalid or has expired — request another")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="account no longer exists")

    problems = password_problems(body.new_password, email=user.email)
    if problems:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"password": problems})

    user.password_hash = hash_password(body.new_password)
    revoke_all(db, user_id)
    # Arriving here also proves control of the inbox, which is what verification proves.
    if user.email_verified_at is None:
        user.email_verified_at = _now()
        user.status = "active"
        user.active = True
    return {"status": "reset"}


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response,
          db: Session = Depends(get_db)) -> dict:
    """Exchange a password for a session. Sets both cookies."""
    rate_limit(request, f"login:{body.email.lower()}", limit=10, per_seconds=900)

    user = db.scalar(select(User).where(User.email == body.email.lower()))
    # The hash is verified even when the user does not exist, against a throwaway value, so the
    # response time does not reveal which addresses have accounts.
    stored = user.password_hash if user else _DUMMY_HASH
    if not verify_password(stored, body.password) or user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=OPAQUE_LOGIN_ERROR)

    if user.status == "pending_verification":
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="confirm your email address first")
    if user.status in ("suspended", "deleted"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="this account is not active")

    if needs_rehash(user.password_hash):
        # The parameters moved since this hash was written. Now is the only moment the plaintext is
        # available to re-hash it.
        user.password_hash = hash_password(body.password)

    session = issue_session(db, user.id,
                            user_agent=request.headers.get("user-agent"),
                            ip=_client_ip(request))
    _set_session_cookies(response, issue_access_token(user.id, session.session_id),
                         session.refresh_token)
    if user.status == "dormant":
        user.status = "active"
    user.last_active_at = _now()
    return {"status": "ok"}


# `response_model=None` because the handler returns either a dict or a Response, and FastAPI
# cannot build a response model from that union.
@router.post("/refresh", response_model=None)
def refresh(request: Request, response: Response,
            db: Session = Depends(get_db)) -> dict | JSONResponse:
    """Rotate the refresh token and mint a new access token."""
    presented = request.cookies.get(REFRESH_COOKIE)
    if not presented:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="no session")

    try:
        rotated = rotate_session(db, presented,
                                 user_agent=request.headers.get("user-agent"),
                                 ip=_client_ip(request))
    except SessionExpired as exc:
        # Returned, not raised. Raising `HTTPException` discards the injected `Response`, so the
        # clear-cookie headers set on it never reach the client — and the client goes on presenting
        # a token that will never work again, forever. Caught by CI: the 401 arrived with no
        # `Set-Cookie` at all.
        failure = JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED,
                               content={"detail": str(exc)})
        _clear_session_cookies(failure)
        return failure

    user_id = db.scalar(select(User.id).where(User.id == _owner(db, rotated.session_id)))
    _set_session_cookies(response, issue_access_token(user_id, rotated.session_id),
                         rotated.refresh_token)
    return {"status": "ok"}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    """End this session. Always answers 200 — logging out twice is not an error."""
    presented = request.cookies.get(REFRESH_COOKIE)
    if presented:
        revoke_session(db, presented)
    _clear_session_cookies(response)
    return {"status": "ok"}


@router.post("/logout-everywhere")
def logout_everywhere(request: Request, response: Response,
                      db: Session = Depends(get_db)) -> dict:
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


def _send_verification(db: Session, mailer: EmailSender, email: str, token: str) -> None:
    """Send the link, and treat any delivery failure as recoverable.

    The account exists either way and the user can request another link. Making registration depend
    on Gmail being reachable — or on there being allowance left today — would turn a provider
    problem into a closed front door.
    """
    # The SPA's hash route, not the API's path. `/auth/verify` is a POST-only API endpoint, so a
    # mail client GETting it lands on a 405 JSON error — which is exactly what the first user to
    # click a real link would have seen.
    url = f"{_base_url()}/#/verify?token={token}"
    subject, body = verification_email(url)
    # The token grants access to the account, so it is scrubbed from anything logged while it is in
    # flight — including a traceback from `smtplib`.
    with redacting(token):
        try:
            send_within_cap(db, mailer, email, subject, body, purpose="verify_email")
        except MailError:
            logger.warning("verification email to %s failed; the account still exists", email,
                           exc_info=True)


def _owner(db: Session, session_id: _uuid.UUID) -> _uuid.UUID:
    return db.scalar(select(UserSession.user_id).where(UserSession.id == session_id))


def _client_ip(request: Request) -> str | None:
    """The peer address. Deliberately not read from `X-Forwarded-For`.

    That header is caller-controlled unless a trusted proxy overwrites it, and this is diagnostic
    data shown on a sessions page — not worth accepting a spoofable value for.
    """
    return request.client.host if request.client else None


def _now() -> datetime:
    return datetime.now(UTC)


# A real argon2 hash of a value nobody has. Verifying against it costs the same as a real check,
# which is the point: a login for an unknown address must not return faster than one for a known.
_DUMMY_HASH = hash_password("not-a-real-password-" + "x" * 24)
