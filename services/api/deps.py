"""Request-scoped dependencies: the database, who is calling, the mailer, and the rate limiter."""
from __future__ import annotations

import hashlib
import logging
import os
import time
import uuid
from datetime import timedelta

from fastapi import Depends, HTTPException, Request, status
from pymongo import ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from libs.auth.access import InvalidAccessToken, read_access_token
from libs.auth.sessions import ACCESS_COOKIE, session_is_live
from libs.db import database, now
from libs.db.models import RateCount
from libs.mail import EmailSender, sender_from_env
from libs.membership import has
from libs.secrets import SecretStore

logger = logging.getLogger(__name__)

_MAILER: EmailSender | None = None


def get_db() -> Database:
    """The platform's database. Each write is its own; a request is not one transaction."""
    return database()


def get_store(db: Database = Depends(get_db)) -> SecretStore:
    """The credential store for this request."""
    return SecretStore(db)


def current_user(request: Request, db: Database = Depends(get_db)) -> uuid.UUID:
    """Who is calling, from the access cookie: a token this server signed, for a session that has not been
    ended. The token alone was enough until 2026-11-20, so signing out, a new password, a replayed refresh
    token and deleting the account each left it good for up to fifteen minutes: for the billing portal, for
    their documents, and for most pages an account that was gone answered 500. One read, by the session's
    id."""
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="not signed in")
    try:
        user_id, session_id = read_access_token(token)
    except InvalidAccessToken as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            detail="your session has expired") from exc
    if not session_is_live(db, user_id, session_id):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="your session has ended")
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


# The time, for the window a request is counted in: its own name so a test can stand it still.
_clock = time.time


def rate_limit(request: Request, key: str, *, limit: int, per_seconds: int) -> None:
    """Fixed window, keyed by caller-supplied string plus the visitor's address, and counted in the database:
    one count for every process the API runs as, which a restart does not begin again. It was kept in each
    process's memory, so with four of them every limit was four times what it said (2026-11-20). One write a
    request, on the routes that ask for it."""
    if os.environ.get("DISABLE_RATE_LIMIT") == "1":
        return
    # The visitor's address as Cloudflare saw it (`CF-Connecting-IP`, set by its edge, which a visitor
    # cannot forge through it), else the peer's. Behind the tunnel the peer is the tunnel, the same for
    # everybody, so a limit keyed on it was one count for the whole site: five sign-ups an hour per API
    # process, for everyone together (found 2026-11-17). Trusted because the API publishes no port of its own
    # (`deploy/compose.yml`): only the tunnel reaches it.
    peer = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "unknown")
    window = int(_clock() // per_seconds)
    counted = hashlib.sha256(f"{key}|{peer}|{per_seconds}|{window}".encode()).hexdigest()
    counts = database()[RateCount.COLLECTION]
    once_more = {"$inc": {"count": 1},
                 "$setOnInsert": {"expires_at": now() + timedelta(seconds=per_seconds)}}
    try:
        row = counts.find_one_and_update({"_id": counted}, once_more, upsert=True,
                                         return_document=ReturnDocument.AFTER)
    except DuplicateKeyError:
        # A window's first two requests at the same moment: the second finds the first one's count.
        row = counts.find_one_and_update({"_id": counted}, once_more, upsert=True,
                                         return_document=ReturnDocument.AFTER)
    if row["count"] > limit:
        # Once a window, the first time it is passed: what stopped somebody, by the limit's name alone (after
        # its ":" is an address or an account), and never a line for each try of a script.
        if row["count"] == limit + 1:
            logger.warning("limit reached: %s, more than %d in %d s", key.partition(":")[0], limit,
                           per_seconds)
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="too many attempts — wait and try again")
