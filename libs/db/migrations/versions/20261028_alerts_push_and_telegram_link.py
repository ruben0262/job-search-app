"""browser push subscriptions, and the one-time code that links a Telegram chat

Revision ID: f49b1ca9b869
Revises: 60ea03bb2a00
Create Date: 2026-10-28 18:00:00.000000

ROADMAP Phase 8, the alerts workstream. New matching jobs reach people off the site through one
platform bot and through browser notifications, each a single tap.

`telegram_connect` is a new `auth_tokens` purpose: the code in the `t.me/<bot>?start=` link that the
platform's webhook redeems to tie a chat to an account. Written by hand because autogenerate does not
see CHECK constraints (`libs/db/README.md`).

`push_subscriptions` is one row per browser that asked for notifications, keyed by the push service's
endpoint, which is what identifies a browser. It cascades with the account like every per-user table.

The per-user bot's columns (`telegram_webhook_secret`, `telegram_routes`) stay: nothing writes them
any more, and dropping a column is not needed to stop using it.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'f49b1ca9b869'
down_revision: str | None = '60ea03bb2a00'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("purpose_valid", "auth_tokens", type_="check")
    op.create_check_constraint(
        "purpose_valid", "auth_tokens",
        "purpose IN ('verify_email', 'reset_password', 'telegram_connect')")

    op.create_table(
        "push_subscriptions",
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("p256dh", sa.String(length=256), nullable=False),
        sa.Column("auth", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE",
                                name=op.f("fk_push_subscriptions_user_id_users")),
        sa.PrimaryKeyConstraint("endpoint", name=op.f("pk_push_subscriptions")),
    )
    op.create_index(op.f("ix_push_subscriptions_user_id"), "push_subscriptions", ["user_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_push_subscriptions_user_id"), table_name="push_subscriptions")
    op.drop_table("push_subscriptions")
    # The old constraint refuses these rows, and an unredeemed link code is worth nothing anyway.
    op.execute("DELETE FROM auth_tokens WHERE purpose = 'telegram_connect'")
    op.drop_constraint("purpose_valid", "auth_tokens", type_="check")
    op.create_check_constraint(
        "purpose_valid", "auth_tokens", "purpose IN ('verify_email', 'reset_password')")
