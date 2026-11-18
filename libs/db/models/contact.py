"""A message sent from the Help page's contact form, or the landing page's: kept here, and emailed to the
support address."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from pymongo import IndexModel

from libs.db import Document, now

# How long a message is kept, then deleted by the database itself: Hungary's consumer protection act has a
# complaint and the answer to it kept three years (Fgytv. 17/A. § (7)), and any message may be one. The
# Privacy Policy states it (`libs.legal.documents`). A year is taken as 366 days, so three years are never
# short.
CONTACT_KEEP_YEARS = 3


@dataclass(kw_only=True)
class ContactMessage(Document):
    COLLECTION = "contact_messages"
    SEQUENCE = True
    INDEXES = (IndexModel("created_at", expireAfterSeconds=CONTACT_KEEP_YEARS * 366 * 86400, name="expires"),)

    id: int | None = None
    # None for somebody not signed in, writing from the landing page (owner, 2026-11-17).
    user_id: uuid.UUID | None
    # Where the reply goes: the account's address when it was sent, or the one a visitor gave.
    email: str
    subject: str
    message: str
    created_at: datetime = field(default_factory=now)
    # When the email to the support address went out; None while it has not. The row is the record
    # either way, and the admin panel lists every one.
    emailed_at: datetime | None = None
