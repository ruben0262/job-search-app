"""Refresh sessions: hashed at rest, rotated on use."""
from __future__ import annotations

import hashlib
import secrets
import uuid as _uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from pymongo.database import Database

from libs.db import insert, now
from libs.db.models import UserSession

ACCESS_TTL = timedelta(minutes=15)
REFRESH_TTL = timedelta(days=30)
# How long after a refresh token is spent it is still honoured, with a session of its own, where later it is
# a theft. Two tabs whose access token ran out together both present the token one of them has just spent, as
# a laptop waking with two tabs open does, and a browser that never got the answer to its refresh presents it
# again: read as a theft, either signed the person out on every device (2026-11-20). Short, because for this
# long a stolen copy of a token just spent earns a session nobody is told about.
ROTATION_GRACE = timedelta(seconds=10)
# The two cookies a session is held in, named once: the API sets and reads them, and the Cookie Policy lists
# them (`libs.legal.disclosure`).
ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"


class SessionExpired(RuntimeError):
    """The refresh token is unknown, expired, revoked, or already rotated. Re-authenticate."""


@dataclass(frozen=True)
class IssuedSession:
    session_id: _uuid.UUID
    user_id: _uuid.UUID
    refresh_token: str          # plaintext, for the cookie. Never stored.
    expires_at: datetime


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_session(db: Database, user_id: _uuid.UUID) -> IssuedSession:
    """Start a session. The plaintext is returned once and never persisted."""
    token = secrets.token_urlsafe(48)
    row = insert(db, UserSession(user_id=user_id, refresh_hash=_hash(token),
                                 expires_at=now() + REFRESH_TTL))
    return IssuedSession(session_id=row.id, user_id=user_id, refresh_token=token, expires_at=row.expires_at)


def rotate_session(db: Database, refresh_token: str) -> IssuedSession:
    """Spend a refresh token and issue its successor. Spending is one atomic write, so a token is spent
    once; presented again within `ROTATION_GRACE` it earns a second session, and after that it ends every
    session its owner has."""
    sessions = db[UserSession.COLLECTION]
    spent = sessions.find_one_and_update(
        {"refresh_hash": _hash(refresh_token), "rotated_at": None, "revoked_at": None,
         "expires_at": {"$gt": now()}},
        {"$set": {"rotated_at": now()}})
    if spent is not None:
        return issue_session(db, spent["user_id"])

    row = sessions.find_one({"refresh_hash": _hash(refresh_token)})
    if row is None:
        raise SessionExpired("unknown refresh token")
    if row["rotated_at"] is not None:
        if (now() - row["rotated_at"] <= ROTATION_GRACE and row["revoked_at"] is None
                and row["expires_at"] > now()):
            return issue_session(db, row["user_id"])
        # Only a *rotated* token counts as a replay.
        revoke_all(db, row["user_id"])
        raise SessionExpired("refresh token replayed — every session for this user was revoked")
    if row["revoked_at"] is not None:
        # An explicit logout, then a stale cookie.
        raise SessionExpired("session was logged out")
    raise SessionExpired("refresh token expired")


def session_is_live(db: Database, user_id: _uuid.UUID, session_id: _uuid.UUID) -> bool:
    """Whether this session of theirs has not been ended: signed out of, ended with every other (a new
    password, a token replayed), or gone with its account. What every request asks, of the session its
    access token names (`services.api.deps.current_user`)."""
    return db[UserSession.COLLECTION].count_documents(
        {"_id": session_id, "user_id": user_id, "revoked_at": None}, limit=1) > 0


def session_owner(db: Database, refresh_token: str) -> _uuid.UUID | None:
    """Who this refresh token belongs to, or `None`. For "log me out everywhere"."""
    row = db[UserSession.COLLECTION].find_one({"refresh_hash": _hash(refresh_token)}, {"user_id": 1})
    return row["user_id"] if row else None


def revoke_session(db: Database, refresh_token: str) -> bool:
    """Log out one device. Returns whether anything was revoked."""
    return bool(db[UserSession.COLLECTION].update_one(
        {"refresh_hash": _hash(refresh_token), "revoked_at": None},
        {"$set": {"revoked_at": now()}}).modified_count)


def revoke_all(db: Database, user_id: _uuid.UUID) -> int:
    """Log out everywhere. Used on password change, on replay detection, and on account deletion."""
    return db[UserSession.COLLECTION].update_many(
        {"user_id": user_id, "revoked_at": None}, {"$set": {"revoked_at": now()}}).modified_count
