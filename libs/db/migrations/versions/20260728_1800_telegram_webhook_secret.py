"""telegram webhook secret

Revision ID: b8e2d05f713c
Revises: a7e3f9c14b62
Create Date: 2026-07-28 18:00:00.000000

The per-user path Telegram posts button taps to.

Per user rather than one shared endpoint because every user runs their own bot: a single path could
not attribute a tap to an account without trusting the request body, and the body is attacker-
supplied. Nullable — an account without one simply has no webhook registered yet.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8e2d05f713c"
down_revision: str | None = "a7e3f9c14b62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("telegram_webhook_secret", sa.String(length=64), nullable=True))
    # Unique: the path *is* the identity, so two accounts sharing one would attribute taps to
    # whichever row the query happened to return first.
    op.create_index("uq_users_webhook_secret", "users", ["telegram_webhook_secret"], unique=True,
                    postgresql_where=sa.text("telegram_webhook_secret IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("uq_users_webhook_secret", table_name="users")
    op.drop_column("users", "telegram_webhook_secret")
