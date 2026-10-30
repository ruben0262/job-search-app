"""What one person has done with one job: sent to them, hidden, applied for, a cover letter built."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from pymongo import IndexModel, ReturnDocument
from pymongo.database import Database

from libs.db import Document, now


@dataclass(kw_only=True)
class UserJobState(Document):
    COLLECTION = "user_job_state"
    INDEXES = (IndexModel([("user_id", 1), ("job_id", 1)], unique=True, name="one_per_job"),
               IndexModel("job_id", name="by_job"),
               IndexModel([("user_id", 1), ("notified_at", 1)], name="sent"))

    user_id: uuid.UUID
    job_id: int
    notified_at: datetime | None = None
    applied_at: datetime | None = None
    cover_built: bool = False
    dismissed_at: datetime | None = None
    created_at: datetime = field(default_factory=now)


def record_state(db: Database, user_id: uuid.UUID, job_id: int, **fields) -> UserJobState:
    """Set fields on this person's row for this job, creating the row with its defaults if it is new."""
    blank = {name: value for name, value in UserJobState(user_id=user_id, job_id=job_id).to_doc().items()
             if name not in fields and name not in ("user_id", "job_id")}
    return UserJobState.from_doc(db[UserJobState.COLLECTION].find_one_and_update(
        {"user_id": user_id, "job_id": job_id}, {"$set": fields, "$setOnInsert": blank},
        upsert=True, return_document=ReturnDocument.AFTER))
