"""How well each job on a person's list fits them, kept so their Jobs page can filter a whole list by it
quickly (owner, 2026-11-16).

Made by `libs.fit.fill`, in the database, from the posting's features and the person's profile, and read by
`libs.fit.at_least`, which works out any score not kept for the profile as it is now: so a missing or old row
makes a page slower, never wrong. Derived data: made again from what it comes from, so not backed up; gone
with the account (`user_id`), and with its job, on the day the job is archived (owner, 2026-11-16: "the same
as the days we let jobs stay on the jobs board as the max amount before archive").
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from pymongo import IndexModel

from libs.db import Document


@dataclass(kw_only=True)
class FitScore(Document):
    COLLECTION = "fit_scores"
    BACKED_UP = False
    INDEXES = (IndexModel([("user_id", 1), ("job_id", 1)], unique=True, name="one_per_job"),
               IndexModel("expires_at", expireAfterSeconds=0, name="expires_with_job"))

    user_id: uuid.UUID
    job_id: int
    # 1 to 10 with a fraction, as the card shows it rounded (`libs.fit.expression`).
    fit: float
    # What it was worked out with (`libs.fit.key`): their profile, the model and the features' version.
    key: str
    # When its job is archived: found then, and kept `corpus_retention_days` (`libs.corpus.archive`).
    expires_at: datetime
