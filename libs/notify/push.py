"""Web Push: the platform's key pair, made the first time anything asks for it and then kept in the database,
and the one way a message goes to a browser (`send`), an alert's or a test's.

Browsers subscribe with the public half (`/push/key`) and every message is signed with the private half, so
the two are one row and there is nothing to set up. Until 2026-11-18 they were three server settings made by
hand; the live site ran without them, and no browser could turn notifications on (CHANGELOG).
"""
from __future__ import annotations

import base64
import json
import logging

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError
from pywebpush import WebPushException, webpush

from libs.db import get, insert
from libs.db.models import PushSubscription, VapidKey
from libs.notify import site_url
from libs.secrets import KeyRing
from libs.telegram import SEND_TIMEOUT_SEC

logger = logging.getLogger(__name__)

# The one pair's id, and what its wrapped private half is bound to.
_PAIR = "vapid"
# How long a push service holds a message for a phone that is asleep or offline: a day, because a job alert is
# still worth reading the next morning.
TTL_SEC = 24 * 3600


def send(subscription: PushSubscription, payload: dict, key: str) -> bool:
    """One message to one browser, signed with the private key (`private_key`). False when its push service
    says the subscription is gone (404, 410); any other refusal is raised."""
    try:
        webpush({"endpoint": subscription.endpoint,
                 "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth}},
                json.dumps(payload),
                vapid_private_key=key,
                # A fresh dict every call: `webpush` writes the audience and expiry into it. The site's own
                # address is how a push service reaches whoever runs it.
                vapid_claims={"sub": site_url()},
                timeout=SEND_TIMEOUT_SEC, ttl=TTL_SEC)
    except WebPushException as exc:
        if exc.status_code in (404, 410):
            return False
        raise
    return True


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
