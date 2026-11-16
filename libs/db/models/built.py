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
    # "instant": made by code until 2026-11-08, and still kept for whoever made one.
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
    # Whether the PDF passed the ATS check (`services.docgen.pdf`: one column, its text read in order);
    # kept so the outcomes people report can say whether passing it helps them hear back.
    ats_ok: bool | None = None
    # Which wording of the prompt wrote it (`libs.prompts.version`) and the model it ran on, None where the
    # account's own tool chose it: the same outcomes, read wording by wording. Never on the document itself.
    prompt_version: str | None = None
    model: str | None = None
    created_at: datetime = field(default_factory=now)
