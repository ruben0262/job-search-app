"""A finished PDF waiting for its download. Ephemeral (D9): emptied after `document_keep_hours`."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from libs.db import Document, now


@dataclass(kw_only=True)
class BuiltDocument(Document):
    COLLECTION = "built_documents"
    BACKED_UP = False
    OWNER = None  # keyed by the queue job, which is the account's and goes with it

    id: int  # the docgen queue job that built it
    filename: str
    content: bytes
    created_at: datetime = field(default_factory=now)
