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
