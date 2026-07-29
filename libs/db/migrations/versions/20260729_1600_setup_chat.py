"""the setup conversation

Revision ID: f8b3d61ca204
Revises: e7a4b2c95d18
Create Date: 2026-07-29 16:00:00.000000

Where a user talks to the setup agents.

**One thread per user, not one per run.** `/setup`, `/setup-prompts` and `/setup-scorer` are three
commands against the same profile, and the second two are explicitly documented as running *after*
the first — `/setup-prompts` refuses to start until `CLAUDE.md` has no placeholders left. Splitting
them into separate conversations would throw away the context each needs and make "come back and
change one thing" mean starting over.

`agent` records which command produced a turn, so the transcript reads as what it is: one long
conversation with a few distinct phases.

Content is stored as text rather than as the API's block structure. The blocks that matter — tool
calls and their results — are replayed from `tool_calls`, and keeping the human-readable half plain
makes the transcript something a support request can quote.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "f8b3d61ca204"
down_revision: str | None = "e7a4b2c95d18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "setup_messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        # Which command was driving when this turn happened — `setup`, `setup-prompts`,
        # `setup-scorer`. Null for a plain message outside any command.
        sa.Column("agent", sa.String(length=32), nullable=True),
        # The assistant's tool calls and their results, replayed into the next request so the model
        # sees what it did. JSONB rather than text because a failed replay is a silent context loss
        # and this is the shape the API expects back.
        sa.Column("tool_calls", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("role IN ('user', 'assistant')", name=op.f("ck_setup_messages_role")),
    )
    # The only query this table has: one user's transcript, oldest first.
    op.create_index("ix_setup_messages_user", "setup_messages", ["user_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_setup_messages_user", table_name="setup_messages")
    op.drop_table("setup_messages")
