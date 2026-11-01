"""A message sent from the Help page's contact form: kept here, and emailed to the support address."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from libs.db import Document, now


@dataclass(kw_only=True)
class ContactMessage(Document):
    COLLECTION = "contact_messages"
    SEQUENCE = True

    id: int | None = None
    user_id: uuid.UUID
    # Where the reply goes: the account's address when it was sent.
    email: str
    subject: str
    message: str
    created_at: datetime = field(default_factory=now)
    # When the email to the support address went out; None while it has not. The row is the record
    # either way, and the admin panel lists every one.
    emailed_at: datetime | None = None
