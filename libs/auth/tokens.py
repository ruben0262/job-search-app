"""Single-use tokens for email verification and password reset."""
from __future__ import annotations

import hashlib
import secrets
import uuid as _uuid
from datetime import timedelta

from pymongo.database import Database

from libs.db import insert, now
from libs.db.models import AuthToken
from libs.db.models.auth import TOKEN_PURPOSES

# Long enough to be unguessable, short enough that the window for an intercepted email is small.
VERIFICATION_TTL = timedelta(hours=24)
RESET_TTL = timedelta(hours=1)


def hash_token(token: str) -> str:
    """SHA-256, hex."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_token(db: Database, user_id: _uuid.UUID, purpose: str) -> str:
    """Mint a token, store only its hash, and return the plaintext for the email. Any earlier token
    for the same purpose is spent, so only the newest link works."""
    if purpose not in TOKEN_PURPOSES:
        raise ValueError(f"{purpose!r} is not one of {TOKEN_PURPOSES}")

    db[AuthToken.COLLECTION].update_many({"user_id": user_id, "purpose": purpose, "used_at": None},
                                         {"$set": {"used_at": now()}})
    token = secrets.token_urlsafe(32)
    ttl = RESET_TTL if purpose == "reset_password" else VERIFICATION_TTL
    insert(db, AuthToken(id=hash_token(token), user_id=user_id, purpose=purpose,
                         expires_at=now() + ttl))
    return token


def consume_token(db: Database, token: str, purpose: str) -> _uuid.UUID | None:
    """Redeem a token exactly once: spending it is one atomic write, so a double click cannot
    spend it twice."""
    row = db[AuthToken.COLLECTION].find_one_and_update(
        {"_id": hash_token(token), "purpose": purpose, "used_at": None, "expires_at": {"$gt": now()}},
        {"$set": {"used_at": now()}})
    return row["user_id"] if row else None


def token_owner(db: Database, token: str, purpose: str) -> _uuid.UUID | None:
    """Whose this unspent, unexpired token is, without spending it — for a step that may still refuse,
    so a refusal leaves the link usable."""
    row = db[AuthToken.COLLECTION].find_one(
        {"_id": hash_token(token), "purpose": purpose, "used_at": None, "expires_at": {"$gt": now()}},
        {"user_id": 1})
    return row["user_id"] if row else None
