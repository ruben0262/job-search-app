"""Whether a person has agreed to the disclosure, asked wherever their text could leave the server.

In `libs` so the API and the docgen worker ask the same question: the worker is where a document's
text actually goes to a provider, and it cannot import the API.
"""
from __future__ import annotations

import uuid as _uuid

from sqlalchemy.orm import Session

from libs.db.models import User
from libs.legal.disclosure import DISCLOSURE_VERSION


def has_consented(session: Session, user_id: _uuid.UUID) -> bool:
    """Agreed to the disclosure as it reads now. A changed disclosure asks everyone again."""
    user = session.get(User, user_id)
    return bool(user and user.processing_consent_at is not None
                and user.processing_consent_version == DISCLOSURE_VERSION)
