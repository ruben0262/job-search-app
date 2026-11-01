"""One account's use of the site proxy today: what its `proxy_mb_per_day` limit counts. The proxy is
the site's (`libs.proxy`); the bandwidth it spends on somebody's LinkedIn searches is still theirs."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from pymongo import IndexModel

from libs.db import Document, now


@dataclass(kw_only=True)
class ProxyUsage(Document):
    COLLECTION = "proxy_usage"
    INDEXES = (IndexModel("user_id", unique=True, name="one_per_user"),)

    user_id: uuid.UUID
    mb_used_today: float = 0.0
    # When their day last started, in their own timezone: the counter goes back to 0 at their midnight.
    usage_reset_at: datetime | None = None
    last_used_at: datetime | None = None
    created_at: datetime = field(default_factory=now)
    updated_at: datetime = field(default_factory=now)
