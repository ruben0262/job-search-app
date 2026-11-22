"""About you: what a person writes about themselves, in their own words, kept as their one document of kind
"history" (`services.api.onboarding_routes`). Their match scores (`libs.fit`), the words suggested to them
(`libs.suggestions`) and their CVs and cover letters (`services.docgen`) are all read from it with their CV,
so the site asks for it, and for detail, until it has some (owner, 2026-11-22: "we need accurate and in
detail information for them to have the benefits this platform provides").
"""
from __future__ import annotations

import uuid as _uuid

from pymongo.database import Database

from libs.db.models import UserDocument

# At this many words or fewer, the site asks for more: a dot on My documents, a note at the top of the bell
# and one over the box, until there are more (owner, 2026-11-22: "150 words or below, then above that its
# fine").
ENOUGH_WORDS = 150
# The most it takes, set high so anybody can write all they want (owner, 2026-11-22: "a high word limit ... so
# people can type away"): some ten pages. Every CV and cover letter is written from all of it.
MOST_WORDS = 5_000


def words(text: str) -> int:
    """How many words, as anybody counts them: what spaces and new lines separate."""
    return len(text.split())


def written(db: Database, user_id: _uuid.UUID) -> int:
    """How many words their About you has: 0 for none."""
    document = db[UserDocument.COLLECTION].find_one({"user_id": user_id, "kind": "history"}, {"text": 1})
    return words(document["text"]) if document else 0
