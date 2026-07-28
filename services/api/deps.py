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

# Process-local, fixed window, self-sweeping — the shape FlowSolutions uses, carried with its
# caveat intact (`AUTH_REUSE.md` §4.1): with more than one `api` replica each holds its own bucket,
# so the effective limit is `limit x replicas`. Fine at one replica and ≤50 invited users; it must
# move to Postgres before scaling out, and until then it is a courtesy rather than an abuse control.
_WINDOWS: dict[str, tuple[float, int]] = {}
_LOCK = threading.Lock()

_MAILER: EmailSender | None = None


def get_db() -> Iterator[Session]:
    """One transaction per request, committed on a clean return.

    The route handlers therefore never commit: a handler that did would break the property every
    write path here depends on — that the effect and its audit trail land together.
    """
    with session_scope() as session:
        yield session


def get_store(session: Session = Depends(get_db)) -> SecretStore:
    """The credential store for this request. Constructed per request, not per process.

    `SecretStore` holds a session, and a process-lifetime one would outlive every transaction it was
    handed. The KEK it reads is process-level and cached by `KeyRing.from_env`.
    """
    return SecretStore(session)


def current_user(request: Request) -> uuid.UUID:
    """Who is calling, from the access cookie. 401 if there is no valid one.

    Reads the cookie rather than an `Authorization` header: the browser is the only client, the
    token is `HttpOnly` so no script can put it in a header anyway, and accepting both would mean
    two authentication paths to keep in step.
    """
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
    """Fixed window, keyed by caller-supplied string plus peer address.

    Keyed on both so one address cannot lock another user out of logging in by burning their
    per-email budget from elsewhere — the email is in the key, but so is the source.
    """
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
        # Sweep opportunistically rather than on a timer: the dictionary is only ever as large as
        # the number of distinct callers inside one window.
        if len(_WINDOWS) > 4096:
            for stale, (start, _) in list(_WINDOWS.items()):
                if now - start >= per_seconds:
                    _WINDOWS.pop(stale, None)

    if count > limit:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="too many attempts — wait and try again")
