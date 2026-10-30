"""The documents a person uploaded, kept as the text read out of them."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from pymongo import IndexModel

from libs.db import Document, now

DOCUMENT_KINDS = ("cv", "cover_letter", "reference", "achievement", "diploma", "linkedin", "history",
                  "other")


@dataclass(kw_only=True)
class UserDocument(Document):
    COLLECTION = "user_documents"
    SEQUENCE = True
    CHOICES = {"kind": DOCUMENT_KINDS}  # noqa: RUF012
    INDEXES = (IndexModel([("user_id", 1), ("uploaded_at", 1)], name="by_user"),
               # The same text uploaded twice is one document.
               IndexModel([("user_id", 1), ("text_sha256", 1)], unique=True, name="one_per_text"))

    id: int | None = None
    user_id: uuid.UUID
    kind: str
    filename: str
    text: str
    text_sha256: str
    chars: int
    uploaded_at: datetime = field(default_factory=now)
