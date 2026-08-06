"""The user's profile, as the files the reference instance keeps on disk."""
from __future__ import annotations

import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from libs.db.base import Base, timestamptz

# What a row's `kind` may be. Ordered as the files are concatenated into a generation call.
PROFILE_FILE_KINDS = ("context", "skill", "prompt", "template")

# Editing one of these changes what counts as a fit, so old labels answer a different question.
SCORING_RELEVANT_FILES = ("label.md", "04-job-evaluation.md")


class UserProfileFile(Base):
    """One markdown (or HTML) file belonging to one user."""

    __tablename__ = "user_profile_files"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    # `context.md`, `01-candidate-profile.md`, `label.md`, `base_cv.html` — the reference's own names.
    name: Mapped[str] = mapped_column(String(64), primary_key=True)

    content: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16))

    # False while the row still holds the shipped scaffold.
    filled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    created_at: Mapped[timestamptz] = mapped_column(server_default=func.now())
    updated_at: Mapped[timestamptz] = mapped_column(server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint(f"kind IN {PROFILE_FILE_KINDS}", name="user_profile_files_kind_valid"),
    )
