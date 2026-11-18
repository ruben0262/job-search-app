"""Request-scoped dependencies: the database, who is calling, the mailer, and the rate limiter."""
from __future__ import annotations

import os
import threading
import time
import uuid

from fastapi import Depends, HTTPException, Request, status
from pymongo.database import Database

from libs.auth.access import InvalidAccessToken, read_access_token
from libs.auth.sessions import ACCESS_COOKIE
from libs.db import database
from libs.mail import EmailSender, sender_from_env
from libs.membership import has
from libs.secrets import SecretStore

# Process-local, fixed window, self-sweeping: with N `api` replicas the limit is N x limit.
_WINDOWS: dict[str, tuple[float, int]] = {}
_LOCK = threading.Lock()

_MAILER: EmailSender | None = None


def get_db() -> Database:
    """The platform's database. Each write is its own; a request is not one transaction."""
    return database()


def get_store(db: Database = Depends(get_db)) -> SecretStore:
    """The credential store for this request."""
    return SecretStore(db)


def current_user(request: Request) -> uuid.UUID:
    """Who is calling, from the access cookie."""
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="not signed in")
    try:
        user_id, _session_id = read_access_token(token)
    except InvalidAccessToken as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            detail="your session has expired") from exc
    return user_id


def with_alerts(db: Database = Depends(get_db), user_id: uuid.UUID = Depends(current_user)) -> uuid.UUID:
    """Who is calling, as `current_user`, if their plan gives alerts (`libs.membership.PLANS`): 402 if not.
    Asked by what turns an alert on, never by what turns one off, so somebody whose plan ended can stop
    theirs."""
    if not has(db, user_id, "alerts"):
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED,
                            detail="Job alerts come with Starter and the membership.")
    return user_id


def get_mailer() -> EmailSender:
    global _MAILER  # noqa: PLW0603 - one sender per process, built lazily
    if _MAILER is None:
        _MAILER = sender_from_env()
    return _MAILER


def rate_limit(request: Request, key: str, *, limit: int, per_seconds: int) -> None:
    """Fixed window, keyed by caller-supplied string plus the visitor's address."""
    if os.environ.get("DISABLE_RATE_LIMIT") == "1":
        return
    # The visitor's address as Cloudflare saw it (`CF-Connecting-IP`, set by its edge, which a visitor
    # cannot forge through it), else the peer's. Behind the tunnel the peer is the tunnel, the same for
    # everybody, so a limit keyed on it was one count for the whole site: five sign-ups an hour per API
    # process, for everyone together (found 2026-11-17). Trusted because the API publishes no port of its own
    # (`deploy/compose.yml`): only the tunnel reaches it.
    peer = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "unknown")
    bucket = f"{key}|{peer}"
    now = time.monotonic()

    with _LOCK:
        started, count = _WINDOWS.get(bucket, (now, 0))
        if now - started >= per_seconds:
            started, count = now, 0
        count += 1
        _WINDOWS[bucket] = (started, count)
        # Swept opportunistically: the dictionary is only as large as the callers inside one window.
        if len(_WINDOWS) > 4096:
            for stale, (start, _) in list(_WINDOWS.items()):
                if now - start >= per_seconds:
                    _WINDOWS.pop(stale, None)

    if count > limit:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="too many attempts — wait and try again")
