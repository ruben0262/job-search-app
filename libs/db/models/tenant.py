"""The per-user tail: scores, seen/notified state, labels, and trained model artifacts."""
from __future__ import annotations

import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from libs.db.base import Base, timestamptz

# Where a label came from.
LABEL_SOURCES = ("llm", "human", "feedback")


class UserJobState(Base):
    """One user's relationship to one corpus row: scored, seen, notified, applied."""

    __tablename__ = "user_job_state"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    # RESTRICT, not CASCADE, on the corpus side.
    job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("corpus.id", ondelete="RESTRICT"), primary_key=True)

    score: Mapped[float | None] = mapped_column(Float)
    scored_at: Mapped[timestamptz | None]
    # Which model produced `score`.
    scored_model_version: Mapped[int | None] = mapped_column(Integer)

    notified_at: Mapped[timestamptz | None]
    # Scored above the cutoff, never sent, and now too old to be worth sending. Written by the
    # notifier's sweep — the one place that looks at a pending match's age (`libs.tenant.repository`).
    retired_at: Mapped[timestamptz | None]
    # The durable "what I applied to" record.
    applied_at: Mapped[timestamptz | None]
    # server_default, not just a Python-side default.
    cover_built: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    dismissed_at: Mapped[timestamptz | None]

    created_at: Mapped[timestamptz] = mapped_column(server_default=func.now())

    __table_args__ = (
        # The feed query: this user's best unnotified scores.
        Index("ix_user_job_state_feed", "user_id", "score"),
        Index("ix_user_job_state_job", "job_id"),
        # The sweep's predicate: what is still waiting to be sent, per account.
        Index("ix_user_job_state_pending", "user_id",
              postgresql_where=text("notified_at IS NULL AND retired_at IS NULL")),
        # What the health check asks: "does this account have anything owed". The index above cannot
        # answer it — on the live platform 95,203 of 101,903 rows satisfy its predicate, so it
        # selects 93% of the table and the planner correctly ignores it. The selective part is
        # `score`: 30,442 of those rows are `score_lane.skip()` tombstones with no score at all, and
        # the rest are ordered so a cutoff becomes a range seek rather than a scan.
        Index("ix_ujs_owed", "user_id", "score",
              postgresql_where=text(
                  "notified_at IS NULL AND retired_at IS NULL AND score IS NOT NULL")),
    )


class UserLabel(Base):
    """A training label for one user on one corpus row."""

    __tablename__ = "user_labels"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    # RESTRICT as for UserJobState: a label whose job vanished is a corrupted training set.
    job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("corpus.id", ondelete="RESTRICT"), primary_key=True)

    label: Mapped[int] = mapped_column(Integer)
    rationale: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), default="llm")
    # 5x for `feedback`, preserved from the reference instance.
    weight: Mapped[float] = mapped_column(Float, default=1.0)

    profile_version: Mapped[str | None] = mapped_column(String(64))
    profile_generation: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[timestamptz] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint(f"source IN {LABEL_SOURCES}", name="source_valid"),
        # The trainer's read: this user's labels for the current generation.
        Index("ix_user_labels_training_set", "user_id", "profile_generation"),
        # Retention's read, and it cannot use the primary key: "does *any* account have a label on
        # this posting" is a lookup by `job_id` alone, and `job_id` is the PK's second column.
        # Measured on 50,000 postings and 2,000 labels: 21.2 ms without this, 10.6 ms with it, and
        # the plan turns a sequential scan of every label into an index-only scan.
        Index("ix_user_labels_job", "job_id"),
        CheckConstraint("weight > 0", name="weight_positive"),
    )


class UserModel(Base):
    """A trained classifier artifact belonging to one user."""

    __tablename__ = "models"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)

    path: Mapped[str] = mapped_column(Text)
    feature_schema_version: Mapped[int] = mapped_column(Integer)
    profile_generation: Mapped[int] = mapped_column(Integer)

    # Text: a HuggingFace model id is third-party-derived and its length is not ours to bound.
    embedder_model: Mapped[str] = mapped_column(Text)
    embedder_dim: Mapped[int] = mapped_column(Integer)
    # Text like corpus.embedding_version: the composite version string exceeds 32 characters.
    embedder_version: Mapped[str] = mapped_column(Text)

    # Training-set size, holdout metrics, label mix.
    metrics: Mapped[dict | None] = mapped_column(JSONB)
    n_labels: Mapped[int | None] = mapped_column(Integer)

    # The pre-score filter, measured from the same labels this artifact was fitted on.
    centroid: Mapped[bytes | None] = mapped_column(LargeBinary)
    centroid_threshold: Mapped[float | None] = mapped_column(Float)

    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[timestamptz] = mapped_column(server_default=func.now())

    __table_args__ = (
        Index("uq_models_user_version", "user_id", "version", unique=True),
        # At most one active artifact per user, enforced by the database rather than by the trainer.
        Index("uq_models_one_active", "user_id", unique=True,
              postgresql_where=text("is_active")),
    )
