"""The only path between a plaintext credential and the database."""
from __future__ import annotations

import uuid as _uuid
from collections.abc import Iterator
from contextlib import contextmanager

from bson import ObjectId
from pymongo.database import Database

from libs.db import now
from libs.db.models import User, UserSecret
from libs.db.models.user import AI_KINDS, SECRET_KINDS
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
    """Envelope-encrypted credential storage.

    Two shapes. One per kind, put and read by kind: the person's Telegram bot token. And the AI accounts,
    several per person (`libs.llm.accounts`), added and read by their id. Nothing here returns any part
    of a secret to be shown: the plaintext only ever goes to the call that uses it.
    """

    def __init__(self, db: Database, keyring: KeyRing | None = None):
        # Loaded from the environment when not supplied, so a missing KEK fails at construction.
        self._secrets = db[UserSecret.COLLECTION]
        self._users = db[User.COLLECTION]
        self._ring = keyring if keyring is not None else KeyRing.from_env()

    # ── writing ──────────────────────────────────────────────────────────────────────────────

    def put(self, user_id: _uuid.UUID, kind: str, value: str, *,
            validated: bool = False) -> None:
        """Store or replace the one credential of a kind a person has one of."""
        self._check_single(kind)
        ciphertext, nonce = self._seal(user_id, kind, value)
        self._secrets.update_one(
            {"user_id": user_id, "kind": kind},
            {"$set": {"ciphertext": ciphertext, "nonce": nonce,
                      "validated_at": now() if validated else None, "updated_at": now()},
             "$setOnInsert": {"_id": ObjectId(), "active": False, "created_at": now()}},
            upsert=True)

    def add(self, user_id: _uuid.UUID, kind: str, value: str, *, label: str,
            validated: bool = False) -> ObjectId:
        """Store one more AI account, not yet in use. Returns its id."""
        if kind not in AI_KINDS:
            raise UnknownSecretKind(f"{kind!r} is not an AI account kind ({AI_KINDS})")
        ciphertext, nonce = self._seal(user_id, kind, value)
        row = UserSecret(user_id=user_id, kind=kind, ciphertext=ciphertext, nonce=nonce, label=label,
                         validated_at=now() if validated else None)
        self._secrets.insert_one(row.to_doc())
        return row.id

    def reseal(self, user_id: _uuid.UUID, account_id: ObjectId, value: str) -> bool:
        """Replace an AI account's secret with a newer one: what its own tool wrote when it renewed the
        sign-in during a call (`libs.llm.cli`). Returns False if the account has gone meanwhile."""
        row = self._secrets.find_one({"_id": account_id, "user_id": user_id}, {"kind": 1})
        if row is None:
            return False
        ciphertext, nonce = self._seal(user_id, row["kind"], value)
        return bool(self._secrets.update_one(
            {"_id": account_id, "user_id": user_id},
            {"$set": {"ciphertext": ciphertext, "nonce": nonce, "updated_at": now()}}).matched_count)

    # ── reading ──────────────────────────────────────────────────────────────────────────────

    def reveal(self, user_id: _uuid.UUID, kind: str) -> Secret | None:
        """Decrypt the one credential of a kind a person has one of."""
        self._check_single(kind)
        return self._open(user_id, self._secrets.find_one({"user_id": user_id, "kind": kind}))

    def reveal_account(self, user_id: _uuid.UUID, account_id: ObjectId) -> Secret | None:
        """Decrypt one AI account's secret, or None if it is not theirs or has gone."""
        return self._open(user_id, self._secrets.find_one(
            {"_id": account_id, "user_id": user_id, "kind": {"$in": list(AI_KINDS)}}))

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
        """Revoke the one credential of a kind a person has one of. Returns whether a row was removed."""
        self._check_single(kind)
        return bool(self._secrets.delete_one({"user_id": user_id, "kind": kind}).deleted_count)

    def purge(self, user_id: _uuid.UUID) -> int:
        """Destroy every credential for a user, then destroy the key that could read them."""
        removed = self._secrets.delete_many({"user_id": user_id}).deleted_count
        self._users.update_one({"_id": user_id}, {"$set": {"wrapped_dek": None}})
        return removed

    # ── KEK rotation ─────────────────────────────────────────────────────────────────────────

    def rewrap(self, user_id: _uuid.UUID) -> bool:
        """Re-wrap this user's DEK under the current primary KEK."""
        wrapped = self._wrapped(user_id)
        if wrapped is None or not self._ring.needs_rewrap(wrapped):
            return False
        dek = self._ring.unwrap_dek(wrapped, user_id=user_id)
        # Only over the value just read, so a concurrent rewrap is not undone.
        return bool(self._users.update_one(
            {"_id": user_id, "wrapped_dek": wrapped},
            {"$set": {"wrapped_dek": self._ring.wrap_dek(dek, user_id=user_id)}}).modified_count)

    def users_needing_rewrap(self) -> list[_uuid.UUID]:
        """Who is still on a retired KEK. Drives the rotation job; safe to run at any time."""
        rows = self._users.find({"wrapped_dek": {"$ne": None}}, {"wrapped_dek": 1})
        return [row["_id"] for row in rows if self._ring.needs_rewrap(row["wrapped_dek"])]

    # ── internals ────────────────────────────────────────────────────────────────────────────

    def _seal(self, user_id: _uuid.UUID, kind: str, value: str) -> tuple[bytes, bytes]:
        value = value.strip()
        if not value:
            # An empty credential stores successfully and then fails at 3 a.m.
            raise ValueError(f"refusing to store an empty {kind} credential")
        return seal(self._dek(user_id, create=True), value, user_id=user_id, kind=kind)

    def _open(self, user_id: _uuid.UUID, row: dict | None) -> Secret | None:
        if row is None:
            return None
        dek = self._dek(user_id, create=False)
        if dek is None:
            # A secret row whose DEK is gone.
            raise SecretsError(f"user {user_id} has a {row['kind']} secret but no wrapped DEK")
        return Secret(open_sealed(dek, row["ciphertext"], row["nonce"], user_id=user_id, kind=row["kind"]),
                      kind=row["kind"])

    def _wrapped(self, user_id: _uuid.UUID) -> bytes | None:
        user = self._users.find_one({"_id": user_id}, {"wrapped_dek": 1})
        if user is None:
            raise SecretsError(f"no such user: {user_id}")
        return user.get("wrapped_dek")

    def _dek(self, user_id: _uuid.UUID, *, create: bool) -> bytes | None:
        wrapped = self._wrapped(user_id)
        if wrapped is None:
            if not create:
                return None
            # Set only where none exists yet, so two writers at once cannot give one account two
            # keys; the loser reads back and uses the winner's.
            self._users.update_one({"_id": user_id, "wrapped_dek": None}, {"$set": {
                "wrapped_dek": self._ring.wrap_dek(self._ring.new_dek(), user_id=user_id)}})
            wrapped = self._wrapped(user_id)
        return self._ring.unwrap_dek(wrapped, user_id=user_id)

    @staticmethod
    def _check_single(kind: str) -> None:
        """A kind a person has exactly one of. The AI accounts are several, added and read by id."""
        if kind not in SECRET_KINDS or kind in AI_KINDS:
            raise UnknownSecretKind(
                f"{kind!r} is not one of the single kinds in {SECRET_KINDS} — AI accounts are added "
                f"with `add` and read with `reveal_account`")
