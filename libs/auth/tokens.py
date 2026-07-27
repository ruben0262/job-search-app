"""Single-use tokens for email verification and password reset.

One rule carries the whole design (`AUTH_REUSE.md` §3): **only the hash is stored.** The plaintext
exists in the email and nowhere else, so a database dump does not let anyone verify an address or
reset a password — the same reasoning as `libs/secrets`, one layer up.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid as _uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from libs.db.models import AuthToken

# Long enough to be unguessable, short enough that the window for an intercepted email is small.
# FlowSolutions uses 24h for verification; a reset is more dangerous, so it gets less.
VERIFICATION_TTL = timedelta(hours=24)
RESET_TTL = timedelta(hours=1)

TOKEN_PURPOSES = ("verify_email", "reset_password")


def hash_token(token: str) -> str:
    """SHA-256, hex. Not a password hash on purpose: the token is 256 bits of `secrets` output, so
    there is nothing to brute-force and a slow KDF would only add latency to every click."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_token(session: Session, user_id: _uuid.UUID, purpose: str) -> str:
    """Mint a token, store only its hash, and return the plaintext for the email.

    Any earlier unused token for the same purpose is revoked: a user who clicks "resend" twice
    should not leave two live links, and the second email is the one they will use.
    """
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
    """Redeem a token exactly once. Returns the user id, or `None` if it is invalid.

    `None` covers unknown, wrong-purpose, expired, and already-used without distinguishing them —
    the difference is useful to someone probing the endpoint and useless to a legitimate user, whose
    next step is the same either way: request a new link.
    """
    row = session.scalar(
        select(AuthToken).where(AuthToken.token_hash == hash_token(token),
                                AuthToken.purpose == purpose))
    if row is None or row.used_at is not None:
        return None
    if row.expires_at <= datetime.now(UTC):
        return None
    row.used_at = func.now()
    return row.user_id
