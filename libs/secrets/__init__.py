"""Envelope encryption for per-user credentials (KEK/DEK)."""
from libs.secrets.keyring import (
    KEK_ENV,
    DecryptionFailed,
    KeyRing,
    KeyUnavailable,
    SecretsError,
    generate_key,
)
from libs.secrets.redaction import (
    MASK,
    Secret,
    SecretRedactingFilter,
    install,
    install_on_handler,
    redact,
    redacting,
)
from libs.secrets.store import SecretStore, UnknownSecretKind

__all__ = [
    "KEK_ENV",
    "MASK",
    "DecryptionFailed",
    "KeyRing",
    "KeyUnavailable",
    "Secret",
    "SecretRedactingFilter",
    "SecretStore",
    "SecretsError",
    "UnknownSecretKind",
    "generate_key",
    "install",
    "install_on_handler",
    "redact",
    "redacting",
]
