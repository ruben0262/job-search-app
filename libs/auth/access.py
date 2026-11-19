"""The 15-minute access token."""
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
        # Refuse rather than fall back to a default.
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
    """Verify and return `(user_id, session_id)`."""
    try:
        claims = jwt.decode(token, _secret(env), algorithms=[ALGORITHM], issuer=ISSUER)
        return _uuid.UUID(claims["sub"]), _uuid.UUID(claims["sid"])
    except InvalidAccessToken:
        raise
    except Exception as exc:
        raise InvalidAccessToken(str(exc)) from exc


def sign_for(user_id: _uuid.UUID, purpose: str, ttl: timedelta, *, env: dict[str, str] | None = None) -> str:
    """A short-lived token naming this account for one purpose (`aud`), such as the `state` a link through
    another site carries there and back. It is not an access token and cannot be read as one: a token with an
    audience is refused where none is asked for."""
    now = datetime.now(UTC)
    return jwt.encode({"sub": str(user_id), "aud": purpose, "iss": ISSUER, "iat": now, "exp": now + ttl},
                      _secret(env), algorithm=ALGORITHM)


def signed_by(token: str, purpose: str, *, env: dict[str, str] | None = None) -> _uuid.UUID:
    """The account a `sign_for` token names, if it was signed here, for this purpose, and has not expired."""
    try:
        claims = jwt.decode(token, _secret(env), algorithms=[ALGORITHM], issuer=ISSUER, audience=purpose)
        return _uuid.UUID(claims["sub"])
    except InvalidAccessToken:
        raise
    except Exception as exc:
        raise InvalidAccessToken(str(exc)) from exc
