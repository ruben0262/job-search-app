"""The six-lane work queue. One table, one claim protocol, one worker pool per lane.

Training, notification delivery, document generation, and backfill labelling are **the same problem**
— bounded work items, per-user fairness, must not starve each other, must survive a restart — so they
get one mechanism rather than four (SCHEDULER_PLAN.md §1).

**Separate worker pools per lane, not one pool with priorities.** That lesson was learned expensively
in the embedder: it first shipped a single priority queue and score latency still collapsed from
0.08 s to 8–15 s under a bulk flood, because priority decides who is picked up *next* and cannot
recall a worker already blocked inside a multi-second call. Head-of-line blocking is defeated by
dedicated workers. A training job holding the CPU cannot be preempted by marking a notification
"high priority".

Postgres rather than Redis because the queue is transactional with the data the job operates on — a
training job and the ``model_version`` bump it produces commit together, so "queue says done, DB says
not" is structurally impossible. Revisit above ~100 users.

Claim, per SCHEDULER_PLAN.md §3::

    UPDATE jobs SET state='claimed', claimed_at=now(), claimed_by=:worker, attempts=attempts+1
    WHERE id = (
      SELECT id FROM jobs
      WHERE lane=:lane AND state='ready' AND run_after<=now()
      ORDER BY run_after
      FOR UPDATE SKIP LOCKED
      LIMIT 1
    ) RETURNING *;
"""
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

LANES = ("notify", "train", "docgen", "label", "process", "score")
JOB_STATES = ("ready", "claimed", "done", "dead")

# Per-lane visibility timeout (SCHEDULER_PLAN.md §4). A sweeper returns `claimed` rows older than
# this to `ready`, so a worker killed mid-job costs one retry and never a lost job. The values track
# how long the lane's work legitimately takes — too short and healthy jobs get duplicated, too long
# and a crash stalls that user for the duration.
# `process` is generous because it is one long Opus call over a whole document set, and a
# sweeper that reclaims it early would charge the user twice for the same profile.
# `score` is short: one batch of 500 rows through an already-loaded model. A long timeout
# here would leave a crashed worker's batch unclaimable for no reason.
CLAIM_TIMEOUT_MIN = {"notify": 2, "train": 30, "docgen": 10, "label": 30, "process": 20,
                     "score": 5}

# A permanently broken job must never retry forever. On the 5th failure the state becomes `dead` and
# an admin alert fires — visible, rather than quietly consuming a worker slot in perpetuity.
MAX_ATTEMPTS = 5


class QueueJob(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    lane: Mapped[str] = mapped_column(String(16))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    # Collapses duplicate pending work. Enqueue is INSERT ... ON CONFLICT DO NOTHING with
    # dedup_key='retrain', so a user marking five labels produces exactly one pending retrain.
    # This is the debounce, enforced by the database rather than by application logic that can race.
    dedup_key: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)

    state: Mapped[str] = mapped_column(String(16), default="ready")
    attempts: Mapped[int] = mapped_column(Integer, default=0)

    run_after: Mapped[timestamptz] = mapped_column(server_default=func.now())
    claimed_at: Mapped[timestamptz | None]
    claimed_by: Mapped[str | None] = mapped_column(Text)
    # Not in SCHEDULER_PLAN.md §3's DDL, but §5's fairness ordering selects on
    # MAX(finished_at) per user per lane — round-robin does not work without it. Added deliberately.
    finished_at: Mapped[timestamptz | None]
    last_error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[timestamptz] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint(f"lane IN {LANES}", name="lane_valid"),
        CheckConstraint(f"state IN {JOB_STATES}", name="state_valid"),
        # The duplicate-collapse index. Partial on the two live states only: once a job is done or
        # dead it must not block a new one being enqueued with the same key.
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
