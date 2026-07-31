"""argon2id, with FlowSolutions' production parameters.

The parameters are the payload here — they are a real tuning decision already made correctly
(`AUTH_REUSE.md` §3) and re-deriving them is how a deployment ends up with defaults nobody checked.

The one deliberate departure is the minimum length. FlowSolutions allows 8 and `AUTH_REUSE.md` §4
called that low for a product holding users' live API keys; this was 12 on that reasoning, and is
**9 since 2026-07-31** at the owner's decision — 12 was turning people away at the door of an
invited beta, which costs more than the marginal entropy buys. What actually defends this surface
is unchanged: argon2id at production parameters, a per-address login rate limit, and the fact that
a stolen password still cannot reveal a stored credential without being re-entered.
"""
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

# time=1, memory=64 MiB, threads=4, keylen=64, salt=32 bytes — verbatim from production.
_HASHER = PasswordHasher(time_cost=1, memory_cost=65536, parallelism=4,
                         hash_len=64, salt_len=32)

MIN_PASSWORD_LENGTH = 9
MAX_PASSWORD_LENGTH = 256

# Not an abuse control — a courtesy. The real defence is argon2 plus rate limiting; this only stops
# the handful of passwords that would otherwise be guessed on the first attempt.
#
# **Every entry must be at least `MIN_PASSWORD_LENGTH` long to do anything**, since a shorter one is
# already refused for its length and would never reach this check. The list was written against a
# 12-character minimum, so the nine-character common passwords were added when the floor dropped.
_OBVIOUS = frozenset({
    "password", "password1", "passw0rd", "123456789012", "qwertyuiop12",
    "letmein12345", "iloveyou1234", "administrator", "welcome12345",
    "password12", "password123", "123456789", "1234567890", "qwerty123",
    "iloveyou1", "letmein123", "welcome123", "admin12345", "abc1234567",
})


def hash_password(password: str) -> str:
    """Hash for storage. The result carries its own parameters, so a future tuning is migratable."""
    return _HASHER.hash(password)


def verify_password(stored: str, password: str) -> bool:
    """Constant-time verify. Returns False rather than raising on a malformed stored hash.

    A malformed hash means a corrupted or hand-edited row, and the only safe reading of it is "this
    password does not match" — raising would turn a data problem into a login endpoint that 500s.
    """
    try:
        return _HASHER.verify(stored, password)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def needs_rehash(stored: str) -> bool:
    """Whether this hash predates the current parameters. Re-hash on next successful login."""
    try:
        return _HASHER.check_needs_rehash(stored)
    except (InvalidHashError, ValueError):
        return True


def password_problems(password: str, *, email: str | None = None) -> list[str]:
    """Everything wrong with this password, as plain sentences. Empty means acceptable.

    All of them at once rather than the first: a form that reveals one rule per attempt is how a
    user ends up trying six times. The strings are user-facing.

    A breached-password check (HIBP k-anonymity) belongs here too and is not implemented — it needs
    a network call, and adding one to the signup path without a timeout and a fail-open decision
    would make registration depend on a third party being up.
    """
    problems: list[str] = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > MAX_PASSWORD_LENGTH:
        problems.append(f"must be at most {MAX_PASSWORD_LENGTH} characters")
    if password.strip() != password:
        problems.append("must not start or end with a space")
    if password.casefold() in _OBVIOUS:
        problems.append("is too common")
    if email and email.split("@")[0].casefold() in password.casefold() and len(password) < 20:
        problems.append("must not contain your email address")
    return problems
