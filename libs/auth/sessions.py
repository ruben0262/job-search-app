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


class SessionExpired(RuntimeError):
    """The refresh token is unknown, expired, revoked, or already rotated. Re-authenticate."""


@dataclass(frozen=True)
class IssuedSession:
    session_id: _uuid.UUID
    refresh_token: str          # plaintext, for the cookie. Never stored.
    expires_at: datetime


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_session(db: Database, user_id: _uuid.UUID) -> IssuedSession:
    """Start a session. The plaintext is returned once and never persisted."""
    token = secrets.token_urlsafe(48)
    row = insert(db, UserSession(user_id=user_id, refresh_hash=_hash(token),
                                 expires_at=now() + REFRESH_TTL))
    return IssuedSession(session_id=row.id, refresh_token=token, expires_at=row.expires_at)


def rotate_session(db: Database, refresh_token: str) -> IssuedSession:
    """Spend a refresh token and issue its successor. Spending is one atomic write, so two tabs
    refreshing at once get one successor and one refusal, never two live sessions from one token."""
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
        # Only a *rotated* token counts as a replay.
        revoke_all(db, row["user_id"])
        raise SessionExpired("refresh token replayed — every session for this user was revoked")
    if row["revoked_at"] is not None:
        # An explicit logout, then a stale cookie.
        raise SessionExpired("session was logged out")
    raise SessionExpired("refresh token expired")


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
