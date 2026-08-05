"""The per-user tail: scores, seen/notified state, labels, and trained model artifacts.

Every table here carries ``user_id ... ON DELETE CASCADE`` and **references job text by
``job_id`` rather than copying it** (TENANT_SAFETY.md §1, PLATFORM_ARCHITECTURE.md §4). That second
rule is worth a number: a label row in the reference instance is 2,937 bytes because it embeds the
description; referencing the corpus instead takes it to roughly 200. At 50 users the difference is
the entire storage argument for the shared design.

The repository layer over these tables takes ``user_id`` as a **required** argument, never as an
optional filter a caller may forget — a query that omits the tenant should fail to typecheck rather
than quietly return someone else's rows. T1 is the release-blocking test for that.
"""
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

# Where a label came from. `feedback` carries 5x weight in training — the reference instance's
# behaviour, preserved deliberately: a user explicitly rating a match is worth more than the LLM's
# opinion of it.
LABEL_SOURCES = ("llm", "human", "feedback")


class UserJobState(Base):
    """One user's relationship to one corpus row: scored, seen, notified, applied.

    Also the delivery-dedup table the notifier depends on. At-least-once is what the queue's claim
    protocol gives, so ``notified_at`` is what stops a retried job re-sending a digest — and it is
    written **in the same transaction** as marking the queue job done, or the guarantee is not one
    (SCHEDULER_PLAN.md §4).
    """

    __tablename__ = "user_job_state"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    # RESTRICT, not CASCADE, on the corpus side. Deleting the account must take this row with it;
    # deleting a *job* must not. `applied_at` here is the durable "what I applied to" record, and a
    # corpus retention sweep that quietly removed it would be exactly the data loss §4 warns about.
    # Retention sets `description`/`embedding` to NULL and keeps the row; anything a user acted on is
    # `pinned` as well. RESTRICT is the backstop for when someone writes a DELETE anyway — it fails
    # loudly instead of taking user history with it.
    job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("corpus.id", ondelete="RESTRICT"), primary_key=True)

    score: Mapped[float | None] = mapped_column(Float)
    scored_at: Mapped[timestamptz | None]
    # Which model produced `score`. Lets a retrain invalidate stale scores without a full rescore,
    # and makes "why did this rank differently yesterday" answerable.
    scored_model_version: Mapped[int | None] = mapped_column(Integer)

    notified_at: Mapped[timestamptz | None]
    # The durable "what I applied to" record. Small, but it is the row a user would be angriest to
    # lose (PLATFORM_ARCHITECTURE.md §4), which is why it is relational rather than a JSONL file.
    #
    # `applied_at` is set by the FIRST build and never moved, matching the reference: a later cover
    # build supersedes the row but must not rewrite the date the user actually applied.
    #
    # Note what is deliberately NOT here: title, company, description, score. The reference's
    # applied.jsonl copies all of them onto every row, which is most of why a label row costs
    # 2,937 bytes there. Here the row references `job_id` and the corpus is `pin()`ned on apply, so
    # the JD survives retention without being duplicated per user (§4).
    applied_at: Mapped[timestamptz | None]
    # server_default, not just a Python-side default. A NOT NULL column added without one cannot be
    # applied to a table that already has rows, and "the table happens to be empty today" is not a
    # migration strategy — it is how the next one fails in production (CODE_STRUCTURE.md §5,
    # additive-first).
    cover_built: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    dismissed_at: Mapped[timestamptz | None]

    created_at: Mapped[timestamptz] = mapped_column(server_default=func.now())

    __table_args__ = (
        # The feed query: this user's best unnotified scores.
        Index("ix_user_job_state_feed", "user_id", "score"),
        Index("ix_user_job_state_job", "job_id"),
    )


class UserLabel(Base):
    """A training label for one user on one corpus row. Upserted on ``(user_id, job_id)``.

    ``profile_version`` and ``profile_generation`` are copied onto the row at write time on purpose.
    A user editing their profile changes what "good match" means, so labels produced under the old
    rubric are not merely old — they are answers to a different question. Recording the generation is
    what lets the trainer select only labels that still apply, instead of quietly training on a
    mixture of two rubrics (PROFILE_CAPTURE_PLAN.md §6.4).
    """

    __tablename__ = "user_labels"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    # RESTRICT for the same reason as UserJobState: a label whose job vanished is not a smaller
    # training set, it is a silently corrupted one. Labelled rows are pinned against trimming; this
    # is the guard for a delete that should never have been written.
    job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("corpus.id", ondelete="RESTRICT"), primary_key=True)

    label: Mapped[int] = mapped_column(Integer)
    rationale: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), default="llm")
    # 5x for `feedback`, preserved from the reference instance. Stored rather than derived so a
    # change to the weighting policy does not silently rewrite the meaning of historical rows.
    weight: Mapped[float] = mapped_column(Float, default=1.0)

    profile_version: Mapped[str | None] = mapped_column(String(64))
    profile_generation: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[timestamptz] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint(f"source IN {LABEL_SOURCES}", name="source_valid"),
        # The trainer's read: this user's labels for the current generation.
        Index("ix_user_labels_training_set", "user_id", "profile_generation"),
        CheckConstraint("weight > 0", name="weight_positive"),
    )


class UserModel(Base):
    """A trained classifier artifact belonging to one user.

    The artifact file itself stays on disk (written atomically, temp + rename, with a ``.bak`` of the
    previous — existing behaviour worth keeping). This row is the metadata the scorer checks *before*
    loading it.

    **The version columns are what make hot-swap safe.** The trainer writes the artifact, inserts
    this row, and bumps ``users.model_version`` in one transaction; the scorer compares on next serve
    and reloads. But it also asserts that ``feature_schema_version``, ``profile_generation``, and the
    embedder's ``(model, dim, version)`` all match what it is about to score against — and **fails
    closed** if any disagree. Two containers that silently disagree on feature construction do not
    crash; they score against different semantics, which is worse than an outage because nothing
    reports it (CODE_STRUCTURE.md §4).
    """

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
    # Text for the same reason as corpus.embedding_version: the composite version string is longer
    # than 32 characters for the model actually in production.
    embedder_version: Mapped[str] = mapped_column(Text)

    # Training-set size, holdout metrics, label mix. Diagnostic, and what the dashboard's "model
    # status" panel reads.
    metrics: Mapped[dict | None] = mapped_column(JSONB)
    n_labels: Mapped[int | None] = mapped_column(Integer)

    # **The pre-score filter, measured from the same labels this artifact was fitted on.** Beside
    # the model rather than on the user so the two cannot drift: a retrain produces both together,
    # and an old model keeps the centroid it was measured with (ROADMAP 6an).
    #
    # NULL is the fail-open and it is load-bearing — an account under `centroid.MIN_POSITIVES`, or
    # a model fitted before this existed, has no centroid, and a caller holding none scores every
    # row rather than none.
    centroid: Mapped[bytes | None] = mapped_column(LargeBinary)
    centroid_threshold: Mapped[float | None] = mapped_column(Float)

    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[timestamptz] = mapped_column(server_default=func.now())

    __table_args__ = (
        Index("uq_models_user_version", "user_id", "version", unique=True),
        # At most one active artifact per user — enforced by the database rather than by the
        # trainer remembering to clear the previous flag before setting the new one.
        Index("uq_models_one_active", "user_id", unique=True,
              postgresql_where=text("is_active")),
    )
