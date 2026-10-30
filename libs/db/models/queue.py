"""The work queue: one collection, one lane per kind of work, claimed atomically by its workers."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from pymongo import IndexModel

from libs.db import Document, now

LANES = ("notify", "docgen")
JOB_STATES = ("ready", "claimed", "done", "dead")
# How long a claim may stand before another worker may take the job, per lane.
CLAIM_TIMEOUT_MIN = {"notify": 2, "docgen": 10}
MAX_ATTEMPTS = 5


@dataclass(kw_only=True)
class QueueJob(Document):
    COLLECTION = "jobs"
    BACKED_UP = False
    SEQUENCE = True
    CHOICES = {"lane": LANES, "state": JOB_STATES}  # noqa: RUF012
    INDEXES = (
        # One pending job per (lane, user, dedup_key): a second enqueue of the same work is a no-op.
        IndexModel([("lane", 1), ("user_id", 1), ("dedup_key", 1)], unique=True, name="pending_dedup",
                   partialFilterExpression={"state": {"$in": ["ready", "claimed"]},
                                            "dedup_key": {"$type": "string"}}),
        IndexModel([("lane", 1), ("state", 1), ("run_after", 1)], name="claimable"),
        IndexModel("claimed_at", partialFilterExpression={"state": "claimed"}, name="claimed"),
        IndexModel([("lane", 1), ("user_id", 1), ("finished_at", 1)],
                   partialFilterExpression={"state": "done"}, name="done_by_user"))

    id: int | None = None
    lane: str
    user_id: uuid.UUID
    dedup_key: str | None = None
    payload: dict = field(default_factory=dict)
    state: str = "ready"
    attempts: int = 0
    run_after: datetime = field(default_factory=now)
    claimed_at: datetime | None = None
    claimed_by: str | None = None
    finished_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime = field(default_factory=now)


@dataclass(kw_only=True)
class LanePause(Document):
    """An operator's pause on one lane (`deploy/scripts/pipeline_switch.py`): enqueuing is refused."""

    COLLECTION = "lane_pauses"
    OWNER = None

    id: str  # the lane
    reason: str | None = None
    paused_at: datetime = field(default_factory=now)


@dataclass(kw_only=True)
class Lock(Document):
    """A named lease (`libs.db.lease`): who may run something that must not run twice at once."""

    COLLECTION = "locks"
    BACKED_UP = False
    OWNER = None

    id: str
    until: datetime
    holder: str
