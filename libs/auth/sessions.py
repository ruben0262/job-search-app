"""Refresh sessions: hashed at rest, rotated on use.

The policy is FlowSolutions' (`AUTH_REUSE.md` §3): 15-minute access tokens, 30-day refresh tokens,
refresh stored hashed and never in plaintext. Rotation on every refresh is what makes a stolen
refresh token detectable — the legitimate client and the thief cannot both keep using it, and the
second one to present the old value finds it already spent.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid as _uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

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


def issue_session(session: Session, user_id: _uuid.UUID, *,
                  user_agent: str | None = None, ip: str | None = None) -> IssuedSession:
    """Start a session. The plaintext is returned once and never persisted."""
    token = secrets.token_urlsafe(48)
    expires_at = datetime.now(UTC) + REFRESH_TTL
    row = UserSession(user_id=user_id, refresh_hash=_hash(token), expires_at=expires_at,
                      user_agent=(user_agent or "")[:400], ip=(ip or "")[:64] or None)
    session.add(row)
    session.flush()
    return IssuedSession(session_id=row.id, refresh_token=token, expires_at=expires_at)


def rotate_session(session: Session, refresh_token: str, *,
                   user_agent: str | None = None, ip: str | None = None) -> IssuedSession:
    """Spend a refresh token and issue its successor. Raises `SessionExpired` if it is not usable.

    **Reuse of an already-rotated token revokes the whole family.** That is the standard response to
    a replayed refresh token: either the client is buggy or the token was stolen, and in the second
    case the thief and the user are now both holding invalid credentials rather than one of them
    silently keeping access.

    A *revoked* token is different and must not trigger it. That is an ordinary logout followed by a
    stale cookie, and treating it as theft would log a user out of every device for closing a tab.
    """
    row = session.scalar(select(UserSession).where(UserSession.refresh_hash == _hash(refresh_token)))
    if row is None:
        raise SessionExpired("unknown refresh token")

    if row.rotated_at is not None:
        # Only a *rotated* token counts as a replay. It was exchanged for a successor, so a second
        # presentation means two parties hold it.
        revoke_all(session, row.user_id)
        raise SessionExpired("refresh token replayed — every session for this user was revoked")
    if row.revoked_at is not None:
        # An explicit logout, then a stale cookie. Ordinary, and not evidence of anything: treating
        # it as a replay would log the user out of every other device for closing a tab.
        raise SessionExpired("session was logged out")
    if row.expires_at <= datetime.now(UTC):
        raise SessionExpired("refresh token expired")

    row.rotated_at = func.now()
    return issue_session(session, row.user_id, user_agent=user_agent, ip=ip)


def session_owner(session: Session, refresh_token: str) -> _uuid.UUID | None:
    """Who this refresh token belongs to, or `None`. For "log me out everywhere"."""
    return session.scalar(
        select(UserSession.user_id).where(UserSession.refresh_hash == _hash(refresh_token)))


def revoke_session(session: Session, refresh_token: str) -> bool:
    """Log out one device. Returns whether anything was revoked."""
    row = session.scalar(select(UserSession).where(UserSession.refresh_hash == _hash(refresh_token)))
    if row is None or row.revoked_at is not None:
        return False
    row.revoked_at = func.now()
    return True


def revoke_all(session: Session, user_id: _uuid.UUID) -> int:
    """Log out everywhere. Used on password change, on replay detection, and on account deletion."""
    return session.query(UserSession).filter(
        UserSession.user_id == user_id, UserSession.revoked_at.is_(None)
    ).update({"revoked_at": func.now()}) or 0
