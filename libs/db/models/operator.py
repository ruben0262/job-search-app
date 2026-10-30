"""Search terms the operator adds to the shared harvest, beside every account's own words."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from pymongo import IndexModel

from libs.db import Document, now


@dataclass(kw_only=True)
class OperatorKeyword(Document):
    COLLECTION = "operator_keywords"
    OWNER = None
    SEQUENCE = True
    INDEXES = (IndexModel([("keyword", 1), ("location", 1)], unique=True, name="one_per_place"),)

    id: int | None = None
    keyword: str
    location: str | None = None
    note: str | None = None
    created_at: datetime = field(default_factory=now)
    created_by: str | None = None
