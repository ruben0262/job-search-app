"""Single-use tokens for email verification and password reset."""
from __future__ import annotations

import hashlib
import secrets
import uuid as _uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from libs.db.models import AuthToken
from libs.db.models.auth import TOKEN_PURPOSES

# Long enough to be unguessable, short enough that the window for an intercepted email is small.
VERIFICATION_TTL = timedelta(hours=24)
RESET_TTL = timedelta(hours=1)



def hash_token(token: str) -> str:
    """SHA-256, hex."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_token(session: Session, user_id: _uuid.UUID, purpose: str) -> str:
    """Mint a token, store only its hash, and return the plaintext for the email."""
    if purpose not in TOKEN_PURPOSES:
        raise ValueError(f"{purpose!r} is not one of {TOKEN_PURPOSES}")

    session.query(AuthToken).filter(
        AuthToken.user_id == user_id, AuthToken.purpose == purpose,
        AuthToken.used_at.is_(None)).update({"used_at": func.now()})

    token = secrets.token_urlsafe(32)
    ttl = RESET_TTL if purpose == "reset_password" else VERIFICATION_TTL
    session.add(AuthToken(token_hash=hash_token(token), user_id=user_id, purpose=purpose,
                          expires_at=datetime.now(UTC) + ttl))
    return token


def consume_token(session: Session, token: str, purpose: str) -> _uuid.UUID | None:
    """Redeem a token exactly once."""
    row = session.scalar(
        select(AuthToken).where(AuthToken.token_hash == hash_token(token),
                                AuthToken.purpose == purpose))
    if row is None or row.used_at is not None:
        return None
    if row.expires_at <= datetime.now(UTC):
        return None
    row.used_at = func.now()
    return row.user_id
