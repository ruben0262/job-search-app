"""The six-lane work queue."""
from __future__ import annotations

import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from libs.db.base import Base, timestamptz

LANES = ("notify", "train", "docgen", "label", "process", "score", "setup")
JOB_STATES = ("ready", "claimed", "done", "dead")

# Per-lane visibility timeout (SCHEDULER_PLAN.md §4).
CLAIM_TIMEOUT_MIN = {"notify": 2, "train": 30, "docgen": 10, "label": 30, "process": 20,
                     "score": 5,
                     # A chat turn is a tool loop against a frontier model with a human waiting on it.
                     "setup": 20}

# A permanently broken job must never retry forever.
MAX_ATTEMPTS = 5


class QueueJob(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    lane: Mapped[str] = mapped_column(String(16))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    # Collapses duplicate pending work.
    dedup_key: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)

    state: Mapped[str] = mapped_column(String(16), default="ready")
    attempts: Mapped[int] = mapped_column(Integer, default=0)

    run_after: Mapped[timestamptz] = mapped_column(server_default=func.now())
    claimed_at: Mapped[timestamptz | None]
    claimed_by: Mapped[str | None] = mapped_column(Text)
    # §5's fairness ordering selects on MAX(finished_at) per user per lane; round-robin needs it.
    finished_at: Mapped[timestamptz | None]
    last_error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[timestamptz] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint(f"lane IN {LANES}", name="lane_valid"),
        CheckConstraint(f"state IN {JOB_STATES}", name="state_valid"),
        # The duplicate-collapse index.
        Index("uq_jobs_pending_dedup", "lane", "user_id", "dedup_key", unique=True,
              postgresql_where=text("state IN ('ready','claimed')")),
        # The claim query's predicate.
        Index("ix_jobs_lane_state_run_after", "lane", "state", "run_after"),
        # The sweeper's: expired claims, per lane.
        Index("ix_jobs_claimed_at", "claimed_at", postgresql_where=text("state = 'claimed'")),
        # The fairness subquery: last completion per user per lane.
        Index("ix_jobs_done_by_user", "lane", "user_id", "finished_at",
              postgresql_where=text("state = 'done'")),
    )
