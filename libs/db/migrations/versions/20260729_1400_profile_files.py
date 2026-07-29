"""per-user profile files

Revision ID: e7a4b2c95d18
Revises: d5f2c8a41b93
Create Date: 2026-07-29 14:00:00.000000

**The profile stops being a JSON schema and becomes the user's own markdown.**

`REBUILD_PLAN.md` in full; the short version is that `ProfileSchema` plus four renderers produced
worse versions of documents that already exist as markdown in the reference instance — nine
uploaded documents yielding zero experience entries, and a CV built from a skills list.

What the reference gets from `CLAUDE.md` and seven skill files auto-loaded off disk, a hosted
platform has to store per user. This is that table: one row per file, keyed by name, holding the
text the setup agents write and the user edits.

**Not the filesystem.** A per-user directory on the box would mean a volume, a backup path, a
permissions model, and a second source of truth the database cannot join against. The files are
read on every generation call and edited from a web page; that is a table.

`kind` records which of the three classes a row belongs to — `context`, `skill`, `prompt`,
`template` — because two of them decide whether an edit re-versions the user's labels and the
answer must be queryable rather than inferred from the filename.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7a4b2c95d18"
down_revision: str | None = "d5f2c8a41b93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_profile_files",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        # The filename as the reference instance knows it: `context.md`, `01-candidate-profile.md`,
        # `label.md`, `base_cv.html`. Keeping the names identical is what makes the agent prompts
        # portable — they name these files in their instructions.
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        # Whether the user has replaced the shipped scaffold yet. A scaffold full of [PLACEHOLDER]
        # tokens must never reach a generation call — it would put "[YOUR NAME]" on a CV.
        sa.Column("filled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        # One row per file per user. The composite key is the whole identity — an id column would
        # allow two `context.md` rows for one account, and nothing downstream could choose between
        # them.
        sa.PrimaryKeyConstraint("user_id", "name"),
        sa.CheckConstraint("kind IN ('context', 'skill', 'prompt', 'template')",
                           name=op.f("ck_user_profile_files_kind_valid")),
    )


def downgrade() -> None:
    op.drop_table("user_profile_files")
