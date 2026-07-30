"""in-app notifications

Revision ID: c4e9f27ab301
Revises: a8d0a15fed0d
Create Date: 2026-07-30 19:00:00.000000

The bell on the dashboard: what happened to this account while nobody was looking at it — a broken
bot token, a finished training run, a password change. Ported in concept from FlowSolutions'
notifications, minus websockets and preferences; the dashboard polls.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4e9f27ab301"
down_revision: str | None = "a8d0a15fed0d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_notifications",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("level", sa.String(length=8), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("url", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("level IN ('info', 'warning', 'error')",
                           name=op.f("ck_user_notifications_level")),
    )
    op.create_index("ix_user_notifications_unread", "user_notifications", ["user_id", "read_at"])
    op.create_index("ix_user_notifications_recent", "user_notifications", ["user_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_user_notifications_recent", table_name="user_notifications")
    op.drop_index("ix_user_notifications_unread", table_name="user_notifications")
    op.drop_table("user_notifications")
