"""A person's own proxy, which is what lets LinkedIn postings reach them (decision D3)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from pymongo import IndexModel

from libs.db import Document, now

PROXY_STATUSES = ("healthy", "quarantined", "disabled")


@dataclass(kw_only=True)
class ProxyCredential(Document):
    COLLECTION = "proxy_credentials"
    CHOICES = {"status": PROXY_STATUSES}  # noqa: RUF012
    INDEXES = (IndexModel("user_id", unique=True, name="one_per_user"),)

    user_id: uuid.UUID
    # Which `user_secrets` kind holds the proxy URL; the URL itself is never stored here.
    secret_ref: str
    mb_used_today: float = 0.0
    usage_reset_at: datetime | None = None
    status: str = "healthy"
    fail_streak: int = 0
    last_error: str | None = None
    last_used_at: datetime | None = None
    created_at: datetime = field(default_factory=now)
    updated_at: datetime = field(default_factory=now)
