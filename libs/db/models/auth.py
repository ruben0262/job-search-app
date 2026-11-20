"""Sessions, one-time tokens, and the mail sent — the storage behind `libs.auth` and `libs.mail`."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from bson import ObjectId
from pymongo import IndexModel

from libs.db import Document, now


@dataclass(kw_only=True)
class UserSession(Document):
    """A signed-in browser. The refresh token is stored only as its hash."""

    COLLECTION = "user_sessions"
    BACKED_UP = False
    # Gone when it runs out, by the database: a row was written at every refresh and none ever left
    # (385 of them for the dev copy's two accounts, 2026-11-20).
    INDEXES = (IndexModel("refresh_hash", unique=True, name="refresh_hash_unique"),
               IndexModel("user_id", name="by_user"),
               IndexModel("expires_at", expireAfterSeconds=0, name="expires"))

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    user_id: uuid.UUID
    refresh_hash: str
    expires_at: datetime
    rotated_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime = field(default_factory=now)


TOKEN_PURPOSES = ("verify_email", "reset_password")


@dataclass(kw_only=True)
class AuthToken(Document):
    """A one-time link: stored as its hash, so a leaked database cannot be used to sign in."""

    COLLECTION = "auth_tokens"
    BACKED_UP = False
    CHOICES = {"purpose": TOKEN_PURPOSES}  # noqa: RUF012
    INDEXES = (IndexModel([("user_id", 1), ("purpose", 1)], name="outstanding"),
               IndexModel("expires_at", expireAfterSeconds=0, name="expires"))

    id: str  # the token's hash
    user_id: uuid.UUID
    purpose: str
    expires_at: datetime
    used_at: datetime | None = None
    created_at: datetime = field(default_factory=now)


MAIL_PURPOSES = ("verify_email", "reset_password", "notice", "contact", "digest")
# How long a sent email stays counted: only the last day is ever read (`libs.mail.quota.WINDOW`), and a week
# says what was sent lately to somebody looking. None ever left.
MAIL_COUNT_KEEP_DAYS = 7


@dataclass(kw_only=True)
class MailSend(Document):
    """One email sent, counted against the sending provider's daily quota (`libs.mail.quota`)."""

    COLLECTION = "mail_sends"
    BACKED_UP = False
    OWNER = None
    CHOICES = {"purpose": MAIL_PURPOSES}  # noqa: RUF012
    INDEXES = (IndexModel("sent_at", expireAfterSeconds=MAIL_COUNT_KEEP_DAYS * 86400, name="recent"),)

    id: ObjectId = field(default_factory=ObjectId)
    purpose: str
    sent_at: datetime = field(default_factory=now)


@dataclass(kw_only=True)
class RateCount(Document):
    """How often one thing was asked for by one address in one window (`services.api.deps.rate_limit`): a
    count every process the API runs as adds to, gone when its window is."""

    COLLECTION = "rate_counts"
    BACKED_UP = False
    OWNER = None
    INDEXES = (IndexModel("expires_at", expireAfterSeconds=0, name="expires"),)

    # What is counted, for whom and in which window, as a hash: an address or an email is not kept for it.
    id: str
    count: int = 0
    expires_at: datetime
