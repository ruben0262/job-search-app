"""Signup, verification, login, refresh, logout."""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from libs.alerts import first_alert
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
from libs.auth.sessions import ACCESS_COOKIE, ACCESS_TTL, REFRESH_COOKIE, REFRESH_TTL, session_owner
from libs.auth.tokens import RESET_TTL, VERIFICATION_TTL, hash_token, spend_all
from libs.db import find_one, get, insert, now
from libs.db.models import AuthToken, User
from libs.legal import LEGAL_VERSION
from libs.mail import (
    EmailSender,
    MailError,
    existing_account_email,
    reset_email,
    send_within_cap,
    verification_email,
)
from libs.notify import site_url
from libs.secrets import redacting
from services.api.deps import get_db, get_mailer, rate_limit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

# Where the refresh cookie is sent: the routes that end or renew a session, all under `/auth`, and no other
# request the site makes. It was `/auth/refresh` alone, so sign-out never received the token it was to end:
# the session lived on at the server for its thirty days, and "log out everywhere" answered 401 to everybody
# (2026-11-20).
REFRESH_PATH = "/auth"
# The path it had until then. A browser signed in before holds its token there for up to `REFRESH_TTL`, and
# would send it beside the new one, the spent beside the live: taken away whenever the cookies are set or
# cleared. Nothing holds one after 2026-12-21; this and its two uses go then.
OLD_REFRESH_PATH = "/auth/refresh"
# Sign-ins one address may try in fifteen minutes, whatever emails it names, counted before a password is
# checked: each check takes 64 MiB (`libs.auth.passwords`), and the limit by email alone never stopped an
# address that changed the email each time. A hundred lets a classroom behind one address sign in together.
LOGINS_PER_ADDRESS = 100
# And for one email from one address, in the same fifteen minutes.
LOGINS_PER_EMAIL = 10
# Sign-ups for one email in an hour. Each for an unconfirmed address mails it a link, as `/auth/resend` does
# with the same limit; counted for every email alike, so the limit does not say which have an account.
SIGNUPS_PER_EMAIL = 3

# The device and the browser a request says it comes from, looked for in this order: Chrome on an iPhone says
# "Safari" too, and Chrome anywhere does. Said beside each sign-up, because a confirmation that never came on
# iPhones came from Safari and not from Chrome on the same phone, and nothing in the log could tell the two
# apart (2026-11-22).
_DEVICES = (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Macintosh", "Mac"),
            ("Windows", "Windows"))
_BROWSERS = (("CriOS", "Chrome"), ("FxiOS", "Firefox"), ("EdgiOS", "Edge"), ("Instagram", "Instagram"),
             ("FBAN", "Facebook"), ("GSA/", "the Google app"), ("Edg/", "Edge"), ("Chrome/", "Chrome"),
             ("Firefox/", "Firefox"), ("Safari/", "Safari"))


def _asked_from(request: Request) -> str:
    """"iPhone Safari", "iPhone Chrome", "Windows Edge": what asked, in two words."""
    said = request.headers.get("user-agent", "")
    device = next((name for token, name in _DEVICES if token in said), "an unknown device")
    return f"{device} {next((name for token, name in _BROWSERS if token in said), 'an unknown browser')}"


def _who(request: Request, email: str) -> str:
    """Who asked, as the log says it: the address's domain, never the rest of it, and what asked."""
    return f"an address at {email.lower().rpartition('@')[2]}, from {_asked_from(request)}"


def _why_refused(db: Database, token: str, purpose: str) -> str:
    """Why a link did not work, for the log, told apart where what the person needs differs: a used link
    whose account is confirmed means signing in; one replaced by a newer link means opening that one."""
    row = db[AuthToken.COLLECTION].find_one({"_id": hash_token(token), "purpose": purpose},
                                            {"user_id": 1, "used_at": 1})
    if row is None or row.get("used_at") is None:
        return "a link unknown, or expired"
    owner = get(db, User, row["user_id"])
    if purpose == "verify_email" and owner is not None and owner.status == "active":
        return "a used link, the account confirmed already"
    return "a link used already, or replaced by a newer one"


# The response every signup and every failed login gets, whatever actually happened.
OPAQUE_SIGNUP = {"status": "check your email"}
OPAQUE_LOGIN_ERROR = "email or password is incorrect"


# What the sign-up page was left on (owner, 2026-11-12: "an option to do the free membership without any
# credit card info being asked after, or the only sub which gives a free 7 day trial"). The account is the
# same either way, and free: "member" only sends the confirmation link on to the payment form.
Plan = Literal["free", "starter", "member"]


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=256)
    # The legal pages' version the page showed beside its box (`libs.legal`): ticking it agrees to the Terms
    # of Service and says the Privacy, Cookie and Data Use policies were read, and is the only way to an
    # account (owner, 2026-11-12; the Terms since 2026-11-18).
    consent_version: str | None = Field(None, max_length=16)
    plan: Plan = "free"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=256)


def _set_session_cookies(response: Response, access: str, refresh: str) -> None:
    response.set_cookie(
        ACCESS_COOKIE, access, httponly=True, secure=True, samesite="lax",
        max_age=int(ACCESS_TTL.total_seconds()), path="/")
    # Strict, and scoped to the routes that consume it.
    response.set_cookie(
        REFRESH_COOKIE, refresh, httponly=True, secure=True, samesite="strict",
        max_age=int(REFRESH_TTL.total_seconds()), path=REFRESH_PATH)
    _forget_old_refresh_cookie(response)


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_PATH)
    _forget_old_refresh_cookie(response)


def _forget_old_refresh_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path=OLD_REFRESH_PATH, httponly=True, secure=True,
                           samesite="strict")


@router.post("/signup", status_code=status.HTTP_202_ACCEPTED)
def signup(body: SignupRequest, request: Request, background: BackgroundTasks,
           db: Database = Depends(get_db),
           mailer: EmailSender = Depends(get_mailer)) -> dict:
    """Create an unverified account and email a link, once the answer has gone.

    An address nobody has confirmed is whoever signs up for it last: their password, and a link of their own
    that spends the earlier ones. It used to stay the first one's, so a stranger who signed up with somebody's
    address before them kept a password to the account that person then confirmed (2026-11-20). An address
    that is confirmed is left exactly as it is, and mailed nothing."""
    # Per address, and an address is often shared: a campus, an office, a phone network. Twenty an hour lets a
    # class sign up together and still stops one machine making accounts by the hundred (2026-11-17; it was
    # five, which behind the tunnel was five for the whole site).
    rate_limit(request, "signup", limit=20, per_seconds=3600)

    if not body.consent_version:
        logger.info("signup refused: %s: the terms box not ticked", _who(request, body.email))
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Tick the box to agree to the Terms.")
    if body.consent_version != LEGAL_VERSION:
        logger.info("signup refused: %s: terms changed since the page opened", _who(request, body.email))
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail="Our terms changed since this page opened. Reload it and read them again.")
    problems = password_problems(body.password, email=body.email)
    if problems:
        # What the eight refused sign-ups of one evening were for, which nothing said (2026-11-22).
        logger.info("signup refused: %s: password %s", _who(request, body.email), " and ".join(problems))
        # The password rules are told to the caller: they are about the value they just chose.
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail={"password": problems})

    email = body.email.lower()
    rate_limit(request, f"signup:{email}", limit=SIGNUPS_PER_EMAIL, per_seconds=3600)
    password_hash = hash_password(body.password)
    try:
        user_id = insert(db, User(email=email, password_hash=password_hash, status="pending_verification",
                                  processing_consent_at=now(), processing_consent_version=LEGAL_VERSION)).id
    except DuplicateKeyError:
        # One write, so an address confirmed a moment ago is not given a new password by it.
        waiting = db[User.COLLECTION].find_one_and_update(
            {"email": email, "status": "pending_verification"},
            {"$set": {"password_hash": password_hash, "processing_consent_at": now(),
                      "processing_consent_version": LEGAL_VERSION, "updated_at": now()}}, {"_id": 1})
        if waiting is None:
            # Confirmed already, and told so in their inbox with the way in, where it was told nothing: the
            # page says the same to everybody, and people who had confirmed signed up again and waited for an
            # email that never came (2026-11-22).
            user = find_one(db, User, {"email": email})
            told = user is not None and user.status == "active"
            if told:
                background.add_task(_send_existing, db, mailer, email,
                                    issue_token(db, user.id, "reset_password"))
            logger.info("signup: %s, which has an account: %s", _who(request, email),
                        "told so by email" if told else "nothing sent")
            return OPAQUE_SIGNUP
        user_id = waiting["_id"]
    else:
        # Their Jobs list, as it fills: the alert every account starts with, waiting for a plan that has
        # alerts.
        first_alert(db, user_id)

    token = issue_token(db, user_id, "verify_email")
    background.add_task(_send_verification, db, mailer, email, token, body.plan)
    logger.info("signup: %s: a link is being sent", _who(request, email))
    return OPAQUE_SIGNUP


class AuthStatus(BaseModel):
    status: str


class ResendRequest(BaseModel):
    email: EmailStr
    plan: Plan = "free"


@router.post("/resend", status_code=status.HTTP_202_ACCEPTED)
def resend(body: ResendRequest, request: Request, background: BackgroundTasks,
           db: Database = Depends(get_db), mailer: EmailSender = Depends(get_mailer)) -> AuthStatus:
    """Email a fresh verification link to an account still waiting for one. Answers the same anyway."""
    email = body.email.lower()
    rate_limit(request, f"resend:{email}", limit=3, per_seconds=3600)

    user = find_one(db, User, {"email": email})
    waiting = user is not None and user.status == "pending_verification"
    confirmed = user is not None and user.status == "active"
    if waiting:
        # The older links go on working: an iPhone's Mail shows the emails of one subject as one conversation,
        # the oldest first, and the link opened was the one a newer had spent, over and over (2026-11-22).
        background.add_task(_send_verification, db, mailer, email,
                            issue_token(db, user.id, "verify_email", spend_older=False), body.plan)
    elif confirmed:
        background.add_task(_send_existing, db, mailer, email, issue_token(db, user.id, "reset_password"))
    logger.info("resend: %s: %s", _who(request, email), "a new link is being sent" if waiting
                else "told it has an account" if confirmed else "nothing sent, no account")
    return AuthStatus(**OPAQUE_SIGNUP)


class VerifyRequest(BaseModel):
    token: str = Field(min_length=1, max_length=256)


@router.post("/verify")
def verify(body: VerifyRequest, request: Request, response: Response,
           db: Database = Depends(get_db)) -> AuthStatus:
    """Redeem a verification link and sign in: opening it proves the inbox, as a password would."""
    user_id = consume_token(db, body.token, "verify_email")
    if user_id is None:
        logger.info("verify refused, from %s: %s", _asked_from(request),
                    _why_refused(db, body.token, "verify_email"))
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="this link is invalid or has expired — request another")

    user = get(db, User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="account no longer exists")
    # The same refusal login gives: a link must not become a way round a suspension.
    if user.status in ("suspended", "deleted"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="this account is not active")
    _verified(db, user)
    # Confirmed: the links still out there have nothing left to confirm, and each would sign in.
    spend_all(db, user.id, "verify_email")
    _sign_in(request, response, db, user)
    logger.info("verify: %s: confirmed and signed in", _who(request, user.email))
    return AuthStatus(status="verified")


class ForgotRequest(BaseModel):
    email: EmailStr


@router.post("/forgot", status_code=status.HTTP_202_ACCEPTED)
def forgot(body: ForgotRequest, request: Request, background: BackgroundTasks,
           db: Database = Depends(get_db), mailer: EmailSender = Depends(get_mailer)) -> dict:
    """Email a reset link, once the answer has gone."""
    rate_limit(request, f"forgot:{body.email.lower()}", limit=3, per_seconds=3600)

    user = find_one(db, User, {"email": body.email.lower()})
    if user is not None and user.status not in ("suspended", "deleted"):
        background.add_task(_send_reset, db, mailer, user.email, issue_token(db, user.id, "reset_password"))
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
        logger.info("reset refused, from %s: %s", _asked_from(request),
                    _why_refused(db, body.token, "reset_password"))
        raise expired

    problems = password_problems(body.new_password, email=user.email)
    if problems:
        logger.info("reset refused: %s: password %s", _who(request, user.email), " and ".join(problems))
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"password": problems})
    if consume_token(db, body.token, "reset_password") != user.id:
        raise expired

    db[User.COLLECTION].update_one({"_id": user.id}, {"$set": {
        "password_hash": hash_password(body.new_password), "updated_at": now()}})
    revoke_all(db, user.id)
    # Arriving here also proves control of the inbox, which is what verification proves.
    if user.email_verified_at is None:
        _verified(db, user)
    logger.info("reset: %s: a new password set", _who(request, user.email))
    return {"status": "reset"}


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response,
          db: Database = Depends(get_db)) -> dict:
    """Exchange a password for a session. Sets both cookies."""
    rate_limit(request, "login", limit=LOGINS_PER_ADDRESS, per_seconds=900)
    rate_limit(request, f"login:{body.email.lower()}", limit=LOGINS_PER_EMAIL, per_seconds=900)

    user = find_one(db, User, {"email": body.email.lower()})
    # The hash is verified even when the user does not exist, so timing reveals no addresses.
    stored = user.password_hash if user else _DUMMY_HASH
    if not verify_password(stored, body.password) or user is None:
        # The visitor is told the same either way; the log, which of the two.
        logger.info("login refused: %s: %s", _who(request, body.email),
                    "no account" if user is None else "wrong password")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=OPAQUE_LOGIN_ERROR)

    if user.status == "pending_verification":
        logger.info("login refused: %s: not confirmed yet", _who(request, body.email))
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="confirm your email address first")
    if user.status in ("suspended", "deleted"):
        logger.info("login refused: %s: the account is %s", _who(request, body.email), user.status)
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
        "email_verified_at": user.email_verified_at or now(), "status": "active",
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

    # The same refusal login gives. A session outlived its account's suspension for as long as it kept
    # refreshing, which is for good.
    user = get(db, User, rotated.user_id)
    if user is None or user.status != "active":
        revoke_all(db, rotated.user_id)
        failure = JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED,
                               content={"detail": "this account is not active"})
        _clear_session_cookies(failure)
        return failure

    _set_session_cookies(response, issue_access_token(rotated.user_id, rotated.session_id),
                         rotated.refresh_token)
    return {"status": "ok"}


@router.post("/logout")
def logout(request: Request, response: Response, db: Database = Depends(get_db)) -> dict:
    """End this session, here and at the server. Always answers 200 — logging out twice is not an error."""
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


# Every email here is sent after the answer has gone (owner, 2026-11-02: "make sure it's fast"). Sent
# inside the request, each waited on the mail server, 0.4–0.8 s to log in to Zoho before a byte of the
# message, and an address with an account answered that much slower than one without: the timing told
# what the opaque answer was written to hide.

def _send(db: Database, mailer: EmailSender, email: str, token: str, *, what: str, purpose: str,
          mail: tuple[str, str, str]) -> None:
    """One email carrying a link, within the platform's cap, any failure recoverable: they can ask again. The
    token grants access to the account, so it is scrubbed from anything logged in flight; the id the email
    leaves with finds it in the mail server's own log, whether it left and what the other end said."""
    subject, body, html = mail
    with redacting(token):
        try:
            sent = send_within_cap(db, mailer, email, subject, body, purpose=purpose, html=html)
        except MailError:
            logger.warning("%s email to %s failed; they can ask again", what, email, exc_info=True)
            return
    if sent:
        logger.info("%s email to %s handed to the mail server as %s", what, email, sent)


def _send_verification(db: Database, mailer: EmailSender, email: str, token: str, plan: Plan) -> None:
    # The token after the "#", the part of an address a browser keeps to itself: it is in no request, so in
    # no log of ours or of anything between, and in no address another site is told the visitor came from.
    # For a membership, on to the payment form once the page is opened.
    url = site_url(f"/verify#token={token}" + ("" if plan == "free" else f"&plan={plan}"))
    _send(db, mailer, email, token, what="verification", purpose="verify_email",
          mail=verification_email(url, VERIFICATION_TTL))


def _send_reset(db: Database, mailer: EmailSender, email: str, token: str) -> None:
    _send(db, mailer, email, token, what="reset", purpose="reset_password",
          mail=reset_email(site_url(f"/reset#token={token}"), RESET_TTL))


def _send_existing(db: Database, mailer: EmailSender, email: str, token: str) -> None:
    """To an address that asked to sign up, or for a new confirmation link, and has an account: the way in,
    and a new password by the reset link it carries, so it is counted as one."""
    _send(db, mailer, email, token, what="already-an-account", purpose="reset_password",
          mail=existing_account_email(site_url("/signin"), site_url(f"/reset#token={token}"), RESET_TTL))


# A real argon2 hash of a value nobody has.
_DUMMY_HASH = hash_password("not-a-real-password-" + "x" * 24)
