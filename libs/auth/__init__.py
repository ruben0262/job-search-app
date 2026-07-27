"""Authentication: password hashing, verification tokens, and refresh sessions.

**Ported design, not ported code** (`AUTH_REUSE.md` §2, option B). FlowSolutions' `internal/auth` is
~3,900 lines of production Go against MongoDB, and lifting it would reintroduce a second language
and a second datastore against decision D1. What is worth having is the ~15 decisions in §3 — each
easy to get subtly wrong, each already settled in production — so those are copied verbatim and the
primitives come from maintained libraries rather than hand-rolled code.

Two things §4 says explicitly not to carry over, both honoured here: the 8-character password
minimum (raised to 12, because this platform holds users' live Claude keys and proxy credentials),
and the v1 hashing path (not ported at all).
"""
from libs.auth.passwords import (
    MIN_PASSWORD_LENGTH,
    hash_password,
    needs_rehash,
    password_problems,
    verify_password,
)
from libs.auth.sessions import (
    ACCESS_TTL,
    REFRESH_TTL,
    SessionExpired,
    issue_session,
    revoke_all,
    revoke_session,
    rotate_session,
    session_owner,
)
from libs.auth.tokens import (
    VERIFICATION_TTL,
    consume_token,
    hash_token,
    issue_token,
)

__all__ = [
    "ACCESS_TTL",
    "MIN_PASSWORD_LENGTH",
    "REFRESH_TTL",
    "VERIFICATION_TTL",
    "SessionExpired",
    "consume_token",
    "hash_password",
    "hash_token",
    "issue_session",
    "issue_token",
    "needs_rehash",
    "password_problems",
    "revoke_all",
    "revoke_session",
    "rotate_session",
    "session_owner",
    "verify_password",
]
