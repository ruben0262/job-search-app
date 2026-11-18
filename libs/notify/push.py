"""The platform's Web Push key pair: made the first time anything asks for it, then kept in the database.

Browsers subscribe with the public half (`/push/key`) and the notifier signs each alert with the private half,
so the two are one row and there is nothing to set up. Until 2026-11-18 they were three server settings made
by hand; the live site ran without them, and no browser could turn notifications on (CHANGELOG).
"""
from __future__ import annotations

import base64
import logging

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from libs.db import get, insert
from libs.db.models import VapidKey
from libs.secrets import KeyRing

logger = logging.getLogger(__name__)

# The one pair's id, and what its wrapped private half is bound to.
_PAIR = "vapid"


def public_key(db: Database) -> str:
    """What a browser subscribes with (`applicationServerKey`): the uncompressed P-256 point, base64url."""
    return _pair(db).public


def private_key(db: Database) -> str:
    """What the notifier signs with: the raw 32-byte scalar, base64url, as `pywebpush` takes it. Held inside
    `libs.secrets.redacting` while it is in use."""
    return _b64(KeyRing.from_env().unwrap_dek(_pair(db).private, user_id=_PAIR))


def _pair(db: Database) -> VapidKey:
    pair = get(db, VapidKey, _PAIR)
    if pair is not None:
        return pair
    made = ec.generate_private_key(ec.SECP256R1())
    public = made.public_key().public_bytes(serialization.Encoding.X962,
                                            serialization.PublicFormat.UncompressedPoint)
    # Wrapped as a data key is, because it is one: 32 bytes under the KEK, bound to what it is.
    secret = made.private_numbers().private_value.to_bytes(32, "big")
    pair = VapidKey(id=_PAIR, public=_b64(public), private=KeyRing.from_env().wrap_dek(secret, user_id=_PAIR))
    try:
        insert(db, pair)
    except DuplicateKeyError:
        # Another process made one at the same moment: the first stored is the pair.
        return get(db, VapidKey, _PAIR)
    logger.info("push: made the platform's Web Push key pair")
    return pair


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
