"""A finished CV or cover letter, as a PDF.

Kept for the person (`saved`) while it fits their `document_storage_mb` allowance, until they delete
it; one for the same job and kind replaces the last. One that did not fit waits only for its download,
`document_keep_hours`, and is then deleted (D9).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from pymongo import IndexModel

from libs.db import Document, now


@dataclass(kw_only=True)
class BuiltDocument(Document):
    COLLECTION = "built_documents"
    # Made again in seconds from what the backup does keep, so the backup leaves the PDFs out.
    BACKED_UP = False
    CHOICES = {"kind": ("cv", "cover"), "mode": ("instant", "ai")}  # noqa: RUF012
    INDEXES = (IndexModel([("user_id", 1), ("job_id", 1)], name="by_job"),
               IndexModel([("saved", 1), ("created_at", 1)], name="purge"))

    id: int  # the docgen queue job that built it
    user_id: uuid.UUID
    job_id: int
    kind: str
    mode: str
    filename: str
    content: bytes
    size: int
    saved: bool
    created_at: datetime = field(default_factory=now)
