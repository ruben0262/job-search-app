"""The operator's platform settings: one document per key, set from the admin panel."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from libs.db import Document, now


@dataclass(kw_only=True)
class PlatformSetting(Document):
    COLLECTION = "platform_settings"
    OWNER = None

    id: str  # the setting's key
    value: Any
    updated_at: datetime = field(default_factory=now)
    updated_by: str | None = None
