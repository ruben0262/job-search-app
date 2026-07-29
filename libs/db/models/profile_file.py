"""The user's profile, as the files the reference instance keeps on disk.

The reference instance grounds every generation call on `CLAUDE.md` plus seven skill files, loaded
off disk by the CLI. A hosted platform has no per-user checkout, so those files live here — one row
each, named exactly as the reference names them, because the setup agents' instructions refer to
them by filename and a rename would make the ported prompts wrong.

**This replaces `users.profile`, the JSON schema.** `REBUILD_PLAN.md` records why: a schema can only
carry what it has fields for, which is how nine uploaded documents produced a profile with no career
in it and a CV built from a skills list.

Three of the four kinds are the classes from that plan, and the fourth is the CV layout:

- `context` — `context.md`, the always-loaded profile document
- `skill` — the numbered files (`01-candidate-profile.md` … `07-interview-prep.md`)
- `prompt` — `label.md`, the labelling rubric the user's own model is trained against
- `template` — `base_cv.html`, their CV with their real experience in it

`kind` is stored rather than parsed from the name because it decides whether an edit re-versions the
user's labels, and that question must be answerable in SQL rather than by a regex over filenames.
"""
from __future__ import annotations

import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from libs.db.base import Base, timestamptz

# What a row's `kind` may be. Ordered as the files are concatenated into a generation call.
PROFILE_FILE_KINDS = ("context", "skill", "prompt", "template")

# Editing one of these changes what counts as a *fit*, so the labels a model was trained on stop
# answering the current question. It does not invalidate them on the spot — it advances a counter,
# and the user is asked (`REBUILD_PLAN.md`, "Retraining is a notification, never a side effect").
#
# Everything else — the context document, the CV, the writing guidance — changes what gets *written*
# and leaves every label valid.
SCORING_RELEVANT_FILES = ("label.md", "04-job-evaluation.md")


class UserProfileFile(Base):
    """One markdown (or HTML) file belonging to one user."""

    __tablename__ = "user_profile_files"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    # `context.md`, `01-candidate-profile.md`, `label.md`, `base_cv.html` — the reference
    # instance's own names, unchanged. The agent prompts name these files in their instructions.
    name: Mapped[str] = mapped_column(String(64), primary_key=True)

    content: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16))

    # False while the row still holds the shipped scaffold. A scaffold is full of `[PLACEHOLDER]`
    # tokens, and one reaching a generation call would put "[YOUR NAME]" on a document somebody
    # sends to an employer — so the resolver refuses to ground on an unfilled file.
    filled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    created_at: Mapped[timestamptz] = mapped_column(server_default=func.now())
    updated_at: Mapped[timestamptz] = mapped_column(server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint(f"kind IN {PROFILE_FILE_KINDS}", name="user_profile_files_kind_valid"),
    )
