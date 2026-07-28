"""Validating a credential at the moment it is saved.

`TENANT_SAFETY.md` §2: *validated at save time — make a real minimal API call. A bad key must fail
at the form, not silently at 3 a.m.*

Kept out of `libs.secrets` on purpose. Storage must not depend on a third party being reachable, so
whether to accept a credential that could not be checked is the caller's decision rather than
something the layering decides for them.
"""
from libs.credentials.validate import (
    CLAUDE_OAUTH_BETA,
    ValidationResult,
    telegram_chat_id,
    validate_credential,
)

__all__ = [
    "CLAUDE_OAUTH_BETA",
    "ValidationResult",
    "telegram_chat_id",
    "validate_credential",
]
