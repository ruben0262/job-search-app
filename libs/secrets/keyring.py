"""The key hierarchy: KEK in the environment, DEK on the user row, secrets in `user_secrets`."""
from __future__ import annotations

import base64
import os
import secrets as _stdlib_secrets
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# AES-256-GCM: 32-byte keys, 12-byte nonces, never parameterised.
KEY_BYTES = 32
NONCE_BYTES = 12

# The environment variable holding the KEK.
KEK_ENV = "SECRETS_KEK"

# Deliberately short and opaque: it ends up in every wrapped DEK, so it is an identifier, not a description.
_MAX_KEY_ID = 16


class SecretsError(RuntimeError):
    """Base for every failure in this package."""


class KeyUnavailable(SecretsError):
    """No usable KEK — refuse to run rather than degrade to something weaker."""


class DecryptionFailed(SecretsError):
    """Ciphertext did not authenticate: wrong key, wrong owner, or tampering."""


def generate_key() -> str:
    """A fresh KEK, printable."""
    return base64.urlsafe_b64encode(AESGCM.generate_key(bit_length=256)).decode().rstrip("=")


def _decode_key(raw: str) -> bytes:
    padded = raw + "=" * (-len(raw) % 4)
    try:
        key = base64.urlsafe_b64decode(padded)
    except Exception as exc:
        # Any decode failure is the same operator error, and a malformed KEK is still key material.
        raise KeyUnavailable(f"{KEK_ENV} entry is not valid base64url") from exc
    if len(key) != KEY_BYTES:
        raise KeyUnavailable(
            f"{KEK_ENV} entry decodes to {len(key)} bytes, expected {KEY_BYTES}")
    return key


@dataclass(frozen=True)
class WrappedDek:
    """A DEK sealed by a KEK, plus the id of the KEK that sealed it."""

    key_id: str
    nonce: bytes
    ciphertext: bytes

    # Version byte first so the format can change without the old rows becoming ambiguous.
    FORMAT_VERSION = 1

    def pack(self) -> bytes:
        key_id = self.key_id.encode("ascii")
        return bytes([self.FORMAT_VERSION, len(key_id)]) + key_id + self.nonce + self.ciphertext

    @classmethod
    def unpack(cls, blob: bytes) -> WrappedDek:
        if not blob or blob[0] != cls.FORMAT_VERSION:
            raise DecryptionFailed("wrapped DEK is not in a format this build understands")
        id_len = blob[1]
        key_id = blob[2:2 + id_len].decode("ascii")
        rest = blob[2 + id_len:]
        if len(rest) <= NONCE_BYTES:
            raise DecryptionFailed("wrapped DEK is truncated")
        return cls(key_id=key_id, nonce=rest[:NONCE_BYTES], ciphertext=rest[NONCE_BYTES:])


def _aad_dek(user_id) -> bytes:
    """Bind a wrapped DEK to its owner, so a DEK cannot be copied onto another user's row."""
    return b"dek:" + str(user_id).encode("ascii")


def _aad_secret(user_id, kind: str) -> bytes:
    """Bind a secret to (owner, kind)."""
    return b"secret:" + str(user_id).encode("ascii") + b":" + kind.encode("ascii")


class KeyRing:
    """The KEKs this process holds. One primary for writing, any number for reading."""

    def __init__(self, keys: dict[str, bytes], primary: str):
        if primary not in keys:
            raise KeyUnavailable(f"primary key id {primary!r} is not in the ring")
        self._keys = dict(keys)
        self._primary = primary

    @property
    def primary_id(self) -> str:
        return self._primary

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> KeyRing:
        """Load the ring from `SECRETS_KEK`."""
        raw = (env if env is not None else os.environ).get(KEK_ENV, "").strip()
        if not raw:
            raise KeyUnavailable(
                f"{KEK_ENV} is not set. Generate one with "
                f'`python -c "from libs.secrets import generate_key; print(generate_key())"` '
                f"and store it OUTSIDE the database and its backups (TENANT_SAFETY.md §2).")

        keys: dict[str, bytes] = {}
        order: list[str] = []
        for raw_entry in raw.split(","):
            entry = raw_entry.strip()
            if not entry:
                continue
            key_id, _, material = entry.partition(":")
            if not material:
                # A bare key with no id: the first deployment should not have to understand rotation.
                key_id, material = "k0", key_id
            key_id = key_id.strip()
            if not key_id or len(key_id) > _MAX_KEY_ID or not key_id.isalnum():
                raise KeyUnavailable(
                    f"key id {key_id!r} must be 1-{_MAX_KEY_ID} alphanumeric characters")
            if key_id in keys:
                raise KeyUnavailable(f"duplicate key id {key_id!r} in {KEK_ENV}")
            keys[key_id] = _decode_key(material.strip())
            order.append(key_id)

        if not keys:
            raise KeyUnavailable(f"{KEK_ENV} contains no usable keys")
        return cls(keys, primary=order[0])

    # ── DEK lifecycle ────────────────────────────────────────────────────────────────────────

    def new_dek(self) -> bytes:
        return AESGCM.generate_key(bit_length=256)

    def wrap_dek(self, dek: bytes, *, user_id) -> bytes:
        """Seal a DEK under the primary KEK, bound to its owner. Returns the packed blob."""
        nonce = _stdlib_secrets.token_bytes(NONCE_BYTES)
        ciphertext = AESGCM(self._keys[self._primary]).encrypt(nonce, dek, _aad_dek(user_id))
        return WrappedDek(self._primary, nonce, ciphertext).pack()

    def unwrap_dek(self, blob: bytes, *, user_id) -> bytes:
        wrapped = WrappedDek.unpack(blob)
        key = self._keys.get(wrapped.key_id)
        if key is None:
            # Distinct from a failed decrypt: putting the retired key back in the ring recovers this.
            raise KeyUnavailable(
                f"this row was wrapped by KEK {wrapped.key_id!r}, which is not in {KEK_ENV}")
        try:
            return AESGCM(key).decrypt(wrapped.nonce, wrapped.ciphertext, _aad_dek(user_id))
        except InvalidTag as exc:
            raise DecryptionFailed("wrapped DEK failed authentication") from exc

    def needs_rewrap(self, blob: bytes) -> bool:
        """True if this DEK was wrapped by a retired KEK. Drives rotation; never fails a read."""
        return WrappedDek.unpack(blob).key_id != self._primary


# ── the inner layer: a secret under a DEK ────────────────────────────────────────────────────

def seal(dek: bytes, plaintext: str, *, user_id, kind: str) -> tuple[bytes, bytes]:
    """Encrypt one credential."""
    nonce = _stdlib_secrets.token_bytes(NONCE_BYTES)
    ciphertext = AESGCM(dek).encrypt(nonce, plaintext.encode("utf-8"), _aad_secret(user_id, kind))
    return ciphertext, nonce


def open_sealed(dek: bytes, ciphertext: bytes, nonce: bytes, *, user_id, kind: str) -> str:
    try:
        return AESGCM(dek).decrypt(nonce, ciphertext, _aad_secret(user_id, kind)).decode("utf-8")
    except InvalidTag as exc:
        raise DecryptionFailed(
            f"secret {kind!r} failed authentication — wrong key, wrong owner, or tampering"
        ) from exc
