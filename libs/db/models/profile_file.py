"""One person's profile files: markdown and HTML the documents are written from (CLAUDE.md §3a)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from pymongo import IndexModel

from libs.db import Document, now

PROFILE_FILE_KINDS = ("context", "skill", "prompt", "template")


@dataclass(kw_only=True)
class UserProfileFile(Document):
    COLLECTION = "user_profile_files"
    CHOICES = {"kind": PROFILE_FILE_KINDS}  # noqa: RUF012
    INDEXES = (IndexModel([("user_id", 1), ("name", 1)], unique=True, name="one_per_name"),)

    user_id: uuid.UUID
    name: str
    content: str
    kind: str
    filled: bool = False
    created_at: datetime = field(default_factory=now)
    # Read by `stale_setup`: a file written before the person last changed their details is stale.
    updated_at: datetime = field(default_factory=now)
