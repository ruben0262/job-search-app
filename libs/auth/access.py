"""The 15-minute access token. HS256 JWT, signed with a key held outside the database.

Short-lived and stateless on purpose: it is checked on every request, and a database lookup per
request is what the refresh session exists to avoid. The cost of statelessness is that revocation
is not instant — a logged-out token stays valid until it expires, which is the reason the TTL is
15 minutes rather than hours (`AUTH_REUSE.md` §3).
"""
from __future__ import annotations

import os
import uuid as _uuid
from datetime import UTC, datetime, timedelta

import jwt

from libs.auth.sessions import ACCESS_TTL

# The name of the variable, not a value. The key itself is never in the tree.
ACCESS_SECRET_ENV = "ACCESS_TOKEN_SECRET"  # noqa: S105
ALGORITHM = "HS256"
ISSUER = "job-search-app"


class InvalidAccessToken(RuntimeError):
    """Expired, tampered with, signed by another key, or simply not a token."""


def _secret(env: dict[str, str] | None = None) -> str:
    value = (env if env is not None else os.environ).get(ACCESS_SECRET_ENV, "").strip()
    if not value:
        # Refuse rather than fall back to a default. A signing key with a known value is not a
        # signing key, and every test of the auth flow would still pass.
        raise InvalidAccessToken(
            f"{ACCESS_SECRET_ENV} is not set. Generate one with "
            f'`python -c "import secrets; print(secrets.token_urlsafe(48))"`.')
    return value


def issue_access_token(user_id: _uuid.UUID, session_id: _uuid.UUID, *,
                       ttl: timedelta = ACCESS_TTL,
                       env: dict[str, str] | None = None) -> str:
    """Mint one. `sid` is carried so a future revocation list has something to key on."""
    now = datetime.now(UTC)
    return jwt.encode(
        {"sub": str(user_id), "sid": str(session_id), "iss": ISSUER,
         "iat": now, "exp": now + ttl},
        _secret(env), algorithm=ALGORITHM)


def read_access_token(token: str, *, env: dict[str, str] | None = None) -> tuple[_uuid.UUID, _uuid.UUID]:
    """Verify and return `(user_id, session_id)`. Raises `InvalidAccessToken` on anything wrong.

    `algorithms` is pinned to one value: accepting the token's own `alg` header is the classic JWT
    forgery, and `none` is a valid value in that header.
    """
    try:
        claims = jwt.decode(token, _secret(env), algorithms=[ALGORITHM], issuer=ISSUER)
        return _uuid.UUID(claims["sub"]), _uuid.UUID(claims["sid"])
    except InvalidAccessToken:
        raise
    except Exception as exc:
        raise InvalidAccessToken(str(exc)) from exc
