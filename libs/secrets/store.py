"""The only path between a plaintext credential and the database.

Everything about the storage rules in `TENANT_SAFETY.md` §2 is enforced here rather than described:

- **Write-only through the API.** Reads return `Secret`, which does not stringify. The API layer
  calls `TenantRepository.secret_metadata`, which selects `last_4` — never `ciphertext`.
- **Never logged.** `use()` registers the plaintext with the redaction filter for exactly the
  window it is live, and drops it again on the way out.
- **Rotatable.** `put()` overwrites in place. No history table, because history means a revoked
  credential stays decryptable.
- **Destroyed on deletion.** `purge()` deletes the rows **and** clears the DEK, so any ciphertext
  surviving in a backup is unreadable.

**`user_id` is required on every method**, matching `TENANT_SAFETY.md` §1.3 — and here it is
load-bearing twice over, because it is also the AES-GCM associated data. A caller who forgot the
tenant does not get another user's row; they get a call that does not compile.
"""
from __future__ import annotations

import uuid as _uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.orm import Session

from libs.db.models import User, UserSecret
from libs.db.models.user import SECRET_KINDS
from libs.secrets.keyring import (
    KeyRing,
    SecretsError,
    open_sealed,
    seal,
)
from libs.secrets.redaction import Secret, register, unregister


class UnknownSecretKind(SecretsError):
    """A kind outside `SECRET_KINDS`. Caught here so a typo cannot create an unreadable row."""


class SecretStore:
    """Envelope-encrypted credential storage for one database session."""

    def __init__(self, session: Session, keyring: KeyRing | None = None):
        # Loaded from the environment when not supplied, so a service that forgot to configure the
        # KEK fails at construction rather than at the first user who saves a credential.
        self._s = session
        self._ring = keyring if keyring is not None else KeyRing.from_env()

    # ── writing ──────────────────────────────────────────────────────────────────────────────

    def put(self, user_id: _uuid.UUID, kind: str, value: str, *,
            validated: bool = False) -> None:
        """Store or replace one credential. The previous ciphertext is overwritten, not versioned.

        `validated` records that a real minimal call against the credential succeeded
        (`TENANT_SAFETY.md` §2). It defaults to False and is set by the caller that made the call —
        this layer deliberately does no network I/O, so that saving a credential cannot be blocked
        by a third party being down.
        """
        self._check_kind(kind)
        value = value.strip()
        if not value:
            # An empty credential stores successfully and then fails at 3 a.m. as an auth error
            # nobody traces back to the form.
            raise ValueError(f"refusing to store an empty {kind} credential")

        dek = self._dek(user_id, create=True)
        ciphertext, nonce = seal(dek, value, user_id=user_id, kind=kind)

        row = self._s.get(UserSecret, {"user_id": user_id, "kind": kind})
        if row is None:
            row = UserSecret(user_id=user_id, kind=kind)
            self._s.add(row)
        row.ciphertext = ciphertext
        row.nonce = nonce
        row.last_4 = value[-4:]
        row.validated_at = datetime.now(UTC) if validated else None

    def mark_validated(self, user_id: _uuid.UUID, kind: str,
                       at: datetime | None = None) -> bool:
        """Record that this credential was proven to work. Returns False if there is no such row."""
        self._check_kind(kind)
        row = self._s.get(UserSecret, {"user_id": user_id, "kind": kind})
        if row is None:
            return False
        row.validated_at = at or datetime.now(UTC)
        return True

    # ── reading ──────────────────────────────────────────────────────────────────────────────

    def reveal(self, user_id: _uuid.UUID, kind: str) -> Secret | None:
        """Decrypt one credential. Prefer `use()` — this leaves nothing registered for redaction.

        Exists for callers that must hold a credential across an await boundary or a retry loop,
        where a `with` block would be the wrong shape. Those callers own the redaction window.
        """
        self._check_kind(kind)
        row = self._s.get(UserSecret, {"user_id": user_id, "kind": kind})
        if row is None:
            return None
        dek = self._dek(user_id, create=False)
        if dek is None:
            # A secret row whose DEK is gone. Only reachable through a partial restore or a manual
            # write, and worth failing loudly on: the alternative is a credential that silently
            # never works.
            raise SecretsError(f"user {user_id} has a {kind} secret but no wrapped DEK")
        return Secret(
            open_sealed(dek, row.ciphertext, row.nonce, user_id=user_id, kind=kind), kind=kind)

    @contextmanager
    def use(self, user_id: _uuid.UUID, kind: str) -> Iterator[Secret | None]:
        """Hold a plaintext for the duration of one call, with logs scrubbed for that window.

        The shape `TENANT_SAFETY.md` §2 asks for: decrypt inside the worker that needs it, in
        memory, for the length of the call. Anything logged inside the block — by this code or by a
        library it calls — has the value masked, and the masking stops when the block does, so the
        registry does not accumulate every credential the process has ever touched.
        """
        secret = self.reveal(user_id, kind)
        if secret is None:
            yield None
            return
        register(secret.reveal())
        try:
            yield secret
        finally:
            unregister(secret.reveal())

    # ── deletion ─────────────────────────────────────────────────────────────────────────────

    def delete(self, user_id: _uuid.UUID, kind: str) -> bool:
        """Revoke one credential. Returns whether a row was removed."""
        self._check_kind(kind)
        result = self._s.execute(
            sa_delete(UserSecret).where(
                UserSecret.user_id == user_id, UserSecret.kind == kind))
        return bool(result.rowcount)

    def purge(self, user_id: _uuid.UUID) -> int:
        """Destroy every credential for a user, then destroy the key that could read them.

        The DEK clear is the part that matters. Deleting rows is undone by restoring a backup;
        deleting the only key that decrypts them is not. That is what makes "destroyed on account
        deletion" true across the backups too, rather than only in the live database.
        """
        removed = self._s.execute(
            sa_delete(UserSecret).where(UserSecret.user_id == user_id)).rowcount
        user = self._s.get(User, user_id)
        if user is not None:
            user.wrapped_dek = None
        return int(removed or 0)

    # ── KEK rotation ─────────────────────────────────────────────────────────────────────────

    def rewrap(self, user_id: _uuid.UUID) -> bool:
        """Re-wrap this user's DEK under the current primary KEK. Returns whether it changed.

        Rotating the KEK touches only `users.wrapped_dek` — one small row per user, no secret is
        re-encrypted, and nothing is decrypted that was not already decryptable. That is the whole
        reason for the envelope: rotation is a key operation, not a data migration.
        """
        user = self._s.get(User, user_id)
        if user is None or user.wrapped_dek is None:
            return False
        if not self._ring.needs_rewrap(user.wrapped_dek):
            return False
        dek = self._ring.unwrap_dek(user.wrapped_dek, user_id=user_id)
        user.wrapped_dek = self._ring.wrap_dek(dek, user_id=user_id)
        return True

    def users_needing_rewrap(self) -> list[_uuid.UUID]:
        """Who is still on a retired KEK. Drives the rotation job; safe to run at any time."""
        rows = self._s.execute(
            select(User.id, User.wrapped_dek).where(User.wrapped_dek.is_not(None)))
        return [uid for uid, blob in rows if self._ring.needs_rewrap(blob)]

    # ── internals ────────────────────────────────────────────────────────────────────────────

    def _dek(self, user_id: _uuid.UUID, *, create: bool) -> bytes | None:
        user = self._s.get(User, user_id)
        if user is None:
            raise SecretsError(f"no such user: {user_id}")
        if user.wrapped_dek is None:
            if not create:
                return None
            dek = self._ring.new_dek()
            user.wrapped_dek = self._ring.wrap_dek(dek, user_id=user_id)
            # Flushed immediately so a concurrent writer in another transaction cannot generate a
            # second DEK for the same user and silently orphan the first one's ciphertexts.
            self._s.flush()
            return dek
        return self._ring.unwrap_dek(user.wrapped_dek, user_id=user_id)

    @staticmethod
    def _check_kind(kind: str) -> None:
        if kind not in SECRET_KINDS:
            raise UnknownSecretKind(
                f"{kind!r} is not one of {SECRET_KINDS} — adding a kind means adding it there, "
                f"where the database CHECK constraint reads it from")
