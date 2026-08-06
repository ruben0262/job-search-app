"""Request-scoped dependencies: the database session, the mailer, and the rate limiter."""
from __future__ import annotations

import os
import threading
import time
import uuid
from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from libs.auth.access import InvalidAccessToken, read_access_token
from libs.db.session import session_scope
from libs.mail import EmailSender, sender_from_env
from libs.secrets import SecretStore

# Process-local, fixed window, self-sweeping: with N `api` replicas the limit is N x limit.
_WINDOWS: dict[str, tuple[float, int]] = {}
_LOCK = threading.Lock()

_MAILER: EmailSender | None = None


def get_db() -> Iterator[Session]:
    """One transaction per request, committed on a clean return."""
    with session_scope() as session:
        yield session


def get_store(session: Session = Depends(get_db)) -> SecretStore:
    """The credential store for this request."""
    return SecretStore(session)


def current_user(request: Request) -> uuid.UUID:
    """Who is calling, from the access cookie."""
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="not signed in")
    try:
        user_id, _session_id = read_access_token(token)
    except InvalidAccessToken as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            detail="your session has expired") from exc
    return user_id


def get_mailer() -> EmailSender:
    global _MAILER  # noqa: PLW0603 - one sender per process, built lazily
    if _MAILER is None:
        _MAILER = sender_from_env()
    return _MAILER


def rate_limit(request: Request, key: str, *, limit: int, per_seconds: int) -> None:
    """Fixed window, keyed by caller-supplied string plus peer address."""
    if os.environ.get("DISABLE_RATE_LIMIT") == "1":
        return
    peer = request.client.host if request.client else "unknown"
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
