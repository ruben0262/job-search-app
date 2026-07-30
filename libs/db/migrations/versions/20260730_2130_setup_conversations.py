"""setup conversations

Revision ID: d1f4a83b6027
Revises: c4e9f27ab301
Create Date: 2026-07-30 21:30:00.000000

Several chats per account instead of one endless thread, each with its own context.

**Existing transcripts are preserved as one conversation per user**, not discarded: whatever was
said during setup is the reason the profile looks the way it does, and it should still be readable.
The backfill runs before the NOT NULL, so a user with messages gets a conversation carrying them
and a user with none gets nothing at all — a conversation is created when somebody starts talking.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d1f4a83b6027"
down_revision: str | None = "c4e9f27ab301"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "setup_conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_setup_conversations_user", "setup_conversations",
                    ["user_id", "last_message_at"])

    op.add_column("setup_messages", sa.Column("conversation_id", sa.Uuid(), nullable=True))

    # One conversation per user who has already said something, carrying their whole transcript.
    op.execute("""
        INSERT INTO setup_conversations (id, user_id, title, created_at, last_message_at)
        SELECT gen_random_uuid(), user_id, 'Setup', MIN(created_at), MAX(created_at)
        FROM setup_messages
        GROUP BY user_id
    """)
    op.execute("""
        UPDATE setup_messages m
        SET conversation_id = c.id
        FROM setup_conversations c
        WHERE c.user_id = m.user_id
    """)

    op.alter_column("setup_messages", "conversation_id", nullable=False)
    op.create_foreign_key("fk_setup_messages_conversation", "setup_messages",
                          "setup_conversations", ["conversation_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_setup_messages_conversation", "setup_messages",
                    ["conversation_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_setup_messages_conversation", table_name="setup_messages")
    op.drop_constraint("fk_setup_messages_conversation", "setup_messages", type_="foreignkey")
    op.drop_column("setup_messages", "conversation_id")
    op.drop_index("ix_setup_conversations_user", table_name="setup_conversations")
    op.drop_table("setup_conversations")
