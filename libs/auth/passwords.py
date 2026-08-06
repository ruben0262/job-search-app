"""argon2id, with FlowSolutions' production parameters."""
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

# time=1, memory=64 MiB, threads=4, keylen=64, salt=32 bytes — verbatim from production.
_HASHER = PasswordHasher(time_cost=1, memory_cost=65536, parallelism=4,
                         hash_len=64, salt_len=32)

MIN_PASSWORD_LENGTH = 9
MAX_PASSWORD_LENGTH = 256

# Not an abuse control — a courtesy.
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
    """Constant-time verify."""
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
    """Everything wrong with this password, as plain sentences."""
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
