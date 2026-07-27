"""Envelope encryption for per-user credentials (KEK/DEK).

Every user brings their own Claude credential, Telegram bot token, and proxy credential. Those are
the highest-consequence rows in the database: a leak spends someone else's money and, for the
proxy, breaches a third party's terms.

Envelope encryption rather than a single key: a per-user data key (DEK) encrypts the secret, and a
key-encryption key (KEK) held outside the database encrypts the DEK. The property that buys is
that a database dump — or a leaked backup — is not sufficient to read any credential.

**The KEK is backed up separately from the database** (OPERATIONS_PLAN.md §7). Backing both up to
the same place fails silently in both directions: together, the backup is as good as plaintext;
apart-but-forgotten, the restore produces a database of unreadable secrets. Verify both halves in
the restore drill, before the first invited user.

Isolation is tested, not assumed — TEST_STRATEGY.md T4 sits in the same commit as this module.

## Using it

```python
from libs.secrets import SecretStore, install

install()                                    # once, at process start, after logging is configured

store = SecretStore(session)                 # KEK read from SECRETS_KEK
store.put(user.id, "claude_api", "sk-ant-…")

with store.use(user.id, "claude_api") as key:
    if key is not None:
        client = anthropic.Anthropic(api_key=key.reveal())   # the only place .reveal() appears
```

Three layers, three files:

- `keyring.py` — the cryptography. KEK loading, DEK wrap/unwrap, sealing a secret. AES-256-GCM,
  with the owner (and kind) bound in as associated data so ciphertext cannot be moved between rows.
- `store.py` — the database layer. `user_id` required on every method, overwrite-not-version on
  rotation, crypto-shredding on deletion.
- `redaction.py` — keeping plaintext out of logs. A `Secret` type that will not stringify, plus a
  logging filter as the backstop for values that reach a log through code we do not own.
"""
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
