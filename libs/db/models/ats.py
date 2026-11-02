"""Company job boards the harvester polls, and the validator that lets a poll ask "changed since?"."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from pymongo import IndexModel

from libs.db import Document, now


@dataclass(kw_only=True)
class AtsToken(Document):
    """One company's board on one ATS. A dead board is kept, not deleted, so it is not rediscovered."""

    COLLECTION = "ats_tokens"
    OWNER = None
    INDEXES = (IndexModel([("ats", 1), ("token", 1)], unique=True, name="one_per_board"),
               IndexModel("ats", partialFilterExpression={"dead_at": None}, name="live"))

    ats: str
    token: str
    first_seen_at: datetime = field(default_factory=now)
    dead_at: datetime | None = None
    # The company's own name, from the list it was found by (`deploy/scripts/ats_companies.txt`): Ashby
    # and Lever say no name, and a job card read "Openai". `None` for a board found from a link.
    name: str | None = None


@dataclass(kw_only=True)
class AtsEtag(Document):
    COLLECTION = "ats_etags"
    BACKED_UP = False
    OWNER = None
    INDEXES = (IndexModel([("ats", 1), ("token", 1)], unique=True, name="one_per_board"),)

    ats: str
    token: str
    etag: str
    updated_at: datetime = field(default_factory=now)
