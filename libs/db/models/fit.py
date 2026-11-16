"""How well each job on a person's list fits them, kept so their Jobs page can filter a whole list by it
quickly (owner, 2026-11-16).

Made by `libs.fit.fill`, in the database, from the posting's features and the person's profile, and read by
`libs.fit.scored`, which works out any score not kept for the profile as it is now: so a missing or old row
makes a page slower, never wrong. Derived data: made again from what it comes from, so not backed up, and gone
with the account (`user_id`) or once older than any posting it could be about.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from pymongo import IndexModel

from libs.db import Document, now

# A score this old is about a posting no page still shows: jobs are kept 30 days at most (owner, 2026-11-09).
EXPIRES_SECONDS = 40 * 24 * 3600


@dataclass(kw_only=True)
class FitScore(Document):
    COLLECTION = "fit_scores"
    BACKED_UP = False
    INDEXES = (IndexModel([("user_id", 1), ("job_id", 1)], unique=True, name="one_per_job"),
               IndexModel("scored_at", expireAfterSeconds=EXPIRES_SECONDS, name="expires"))

    user_id: uuid.UUID
    job_id: int
    # 1 to 10 with a fraction, as the card shows it rounded (`libs.fit.expression`).
    fit: float
    # What it was worked out with (`libs.fit.key`): their profile, the model and the features' version.
    key: str
    scored_at: datetime = field(default_factory=now)
