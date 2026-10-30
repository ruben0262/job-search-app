"""One AI request the platform paid for, counted against the daily allowance (`libs.llm.providers`)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from bson import ObjectId
from pymongo import IndexModel

from libs.db import Document, now


@dataclass(kw_only=True)
class AiRequest(Document):
    COLLECTION = "ai_requests"
    BACKED_UP = False
    INDEXES = (IndexModel([("user_id", 1), ("created_at", 1)], name="by_user_day"),)

    id: ObjectId = field(default_factory=ObjectId)
    user_id: uuid.UUID
    created_at: datetime = field(default_factory=now)
