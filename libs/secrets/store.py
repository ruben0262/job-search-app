"""The only path between a plaintext credential and the database."""
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
        # Loaded from the environment when not supplied, so a missing KEK fails at construction.
        self._s = session
        self._ring = keyring if keyring is not None else KeyRing.from_env()

    # ── writing ──────────────────────────────────────────────────────────────────────────────

    def put(self, user_id: _uuid.UUID, kind: str, value: str, *,
            validated: bool = False) -> None:
        """Store or replace one credential."""
        self._check_kind(kind)
        value = value.strip()
        if not value:
            # An empty credential stores successfully and then fails at 3 a.m.
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
        """Decrypt one credential."""
        self._check_kind(kind)
        row = self._s.get(UserSecret, {"user_id": user_id, "kind": kind})
        if row is None:
            return None
        dek = self._dek(user_id, create=False)
        if dek is None:
            # A secret row whose DEK is gone.
            raise SecretsError(f"user {user_id} has a {kind} secret but no wrapped DEK")
        return Secret(
            open_sealed(dek, row.ciphertext, row.nonce, user_id=user_id, kind=kind), kind=kind)

    @contextmanager
    def use(self, user_id: _uuid.UUID, kind: str) -> Iterator[Secret | None]:
        """Hold a plaintext for the duration of one call, with logs scrubbed for that window."""
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
        """Destroy every credential for a user, then destroy the key that could read them."""
        removed = self._s.execute(
            sa_delete(UserSecret).where(UserSecret.user_id == user_id)).rowcount
        user = self._s.get(User, user_id)
        if user is not None:
            user.wrapped_dek = None
        return int(removed or 0)

    # ── KEK rotation ─────────────────────────────────────────────────────────────────────────

    def rewrap(self, user_id: _uuid.UUID) -> bool:
        """Re-wrap this user's DEK under the current primary KEK."""
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
            # Flushed immediately so a concurrent writer cannot generate a second DEK for the same user.
            self._s.flush()
            return dek
        return self._ring.unwrap_dek(user.wrapped_dek, user_id=user_id)

    @staticmethod
    def _check_kind(kind: str) -> None:
        if kind not in SECRET_KINDS:
            raise UnknownSecretKind(
                f"{kind!r} is not one of {SECRET_KINDS} — adding a kind means adding it there, "
                f"where the database CHECK constraint reads it from")
