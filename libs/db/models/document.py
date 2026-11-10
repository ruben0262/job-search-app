"""The documents a person uploaded: the text read out of them, and the file itself to download again."""
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
    # Switched off by its owner: kept, listed, and left out of everything made from their documents.
    in_use: bool = True
    uploaded_at: datetime = field(default_factory=now)


@dataclass(kw_only=True)
class UploadedFile(Document):
    """The file a document's text was read from, as it was uploaded, so its owner can download it again
    (owner, 2026-11-10). A collection of its own, so reading someone's documents never loads their files.
    One per document, under the document's id; what was uploaded before then kept only its text."""
    COLLECTION = "uploaded_files"
    INDEXES = (IndexModel([("user_id", 1)], name="by_user"),)

    id: int  # the `user_documents` row read from it
    user_id: uuid.UUID
    content: bytes
