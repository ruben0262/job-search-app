"""Shadow scores — the judge's opinion of a job, kept beside the model's, on a sample."""
from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from libs.db.base import Base, timestamptz


class ShadowScore(Base):
    """One (user, job) pair the model and the judge both scored."""

    __tablename__ = "shadow_scores"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("corpus.id", ondelete="CASCADE"), primary_key=True)

    # What the served model said, and what the judge said about the same posting.
    model_score: Mapped[float | None] = mapped_column(Float)
    judge_score: Mapped[float | None] = mapped_column(Float)

    # Whether they told the same story, within tolerance.
    agreed: Mapped[bool | None] = mapped_column(Boolean)

    # The judge's one-line read.
    rationale: Mapped[str | None] = mapped_column(String(500))

    # Which model and which question.
    model_version: Mapped[int | None] = mapped_column(Integer)
    profile_generation: Mapped[int | None] = mapped_column(SmallInteger)

    created_at: Mapped[timestamptz] = mapped_column(server_default=func.now())

    __table_args__ = (
        # The one query this table exists to serve: how well the model has agreed with the judge lately.
        Index("ix_shadow_scores_user_recent", "user_id", text("created_at DESC")),
    )
