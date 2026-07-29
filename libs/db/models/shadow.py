"""Shadow scores — the judge's opinion of a job, kept beside the model's, on a sample.

**This is the platform's only way to notice a model going wrong.** A trained model serves scores
for months without an error, a warning, or any signal at all when the numbers stop meaning what
they meant. Sampling 10% of what it scores back through the judge produces the one thing that can
say so: two independent opinions of the same job, timestamped, with the model version attached.

The disagreements pay twice — they are the evidence that a retrain is due, and they are the jobs
most worth asking the user to rate, because that is where a label teaches the most.

The row is per (user, job) rather than per comparison: a re-run must not accumulate duplicates, and
the sampling is deterministic so the same pair is drawn every time (`libs/scoring/shadow.py`).

`model_version` and `profile_generation` are copied on at write time for the reason every other
table here does it — "did these two opinions come from the same question" has to be answerable
later in SQL, not reconstructed from timestamps.
"""
from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Float, ForeignKey, Integer, SmallInteger, String, func
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

    # Whether they told the same story, within tolerance. Nullable because "we never asked" and
    # "they disagreed" are different facts and collapsing them would make a quiet outage look like
    # a drifting model.
    agreed: Mapped[bool | None] = mapped_column(Boolean)

    # The judge's one-line read. Kept because a disagreement is only actionable if you can see what
    # the judge thought the job was.
    rationale: Mapped[str | None] = mapped_column(String(500))

    # Which model and which question. A pair collected before a retrain says nothing about the
    # model serving now, and one collected under an older rubric answers a different question.
    model_version: Mapped[int | None] = mapped_column(Integer)
    profile_generation: Mapped[int | None] = mapped_column(SmallInteger)

    created_at: Mapped[timestamptz] = mapped_column(server_default=func.now())
