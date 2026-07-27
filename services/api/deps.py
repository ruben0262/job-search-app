"""Request-scoped dependencies: the database session, the mailer, and the rate limiter."""
from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from libs.db.session import session_scope
from libs.mail import EmailSender, sender_from_env

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
