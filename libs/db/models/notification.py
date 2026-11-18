"""The bell's in-app notifications, and the browsers that asked for Web Push."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from pymongo import IndexModel

from libs.db import Document, now


@dataclass(kw_only=True)
class UserNotification(Document):
    COLLECTION = "user_notifications"
    SEQUENCE = True
    CHOICES = {"level": ("info", "warning", "error")}  # noqa: RUF012
    INDEXES = (IndexModel([("user_id", 1), ("read_at", 1)], name="unread"),
               IndexModel([("user_id", 1), ("_id", 1)], name="recent"))

    id: int | None = None
    user_id: uuid.UUID
    event: str
    level: str = "info"
    title: str
    body: str = ""
    url: str | None = None
    created_at: datetime = field(default_factory=now)
    read_at: datetime | None = None


@dataclass(kw_only=True)
class PushSubscription(Document):
    """One browser's Web Push subscription. The endpoint is the browser's own address, so it is unique."""

    COLLECTION = "push_subscriptions"
    INDEXES = (IndexModel("endpoint", unique=True, name="one_per_browser"),
               IndexModel("user_id", name="by_user"))

    endpoint: str
    user_id: uuid.UUID
    p256dh: str
    auth: str
    created_at: datetime = field(default_factory=now)


@dataclass(kw_only=True)
class VapidKey(Document):
    """The platform's one Web Push key pair (`libs.notify.push`). Kept beside the subscriptions made with it
    and backed up with them, so a restore brings back both: a browser subscribed with one pair hears nothing
    signed with another. The private half is wrapped under `SECRETS_KEK`, which never enters the database."""

    COLLECTION = "vapid_keys"
    OWNER = None

    id: str
    public: str
    private: bytes
    created_at: datetime = field(default_factory=now)
