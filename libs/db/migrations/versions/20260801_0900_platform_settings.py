"""Operator-owned settings, and the per-user fields they replace.

**The product streams the newest postings, so freshness and cadence are properties of the platform
rather than preferences.** A user choosing "every four hours" was asking to be told about a
two-hour-old job, which contradicts what the thing is for; and a per-user freshness window meant
every source had to be fetched at the widest window anybody asked for and filtered back down per
person, which is the complexity this removes.

So: one key-value table the operator writes, and two user columns deleted rather than left unread.
Dropping them is deliberate — a field that still exists but nothing consults is a field the next
reader will wire back up by accident.

`user_config.sections[].posted_within_hours` goes the same way, stripped from the stored JSON so no
config carries a number that no longer does anything.

Revision ID: a1c8e5f20b74
Revises: f3a71c2d4e90
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1c8e5f20b74"
down_revision: str | None = "f3a71c2d4e90"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "platform_settings",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_by", sa.String(length=320)),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_platform_settings")),
    )

    # The per-user fields these replace.
    op.drop_constraint("notify_interval_bounds", "users", type_="check")
    op.drop_column("users", "notify_interval_min")

    # And the same number where it hid inside the config blob. Written as a JSONB rewrite rather
    # than left in place: a stored section carrying `posted_within_hours` after this migration is a
    # number that looks live and is not.
    op.execute("""
        UPDATE user_config
           SET config = jsonb_set(
                 config - 'posted_within_hours',
                 '{sections}',
                 coalesce((SELECT jsonb_agg(section - 'posted_within_hours')
                             FROM jsonb_array_elements(config->'sections') AS section),
                          '[]'::jsonb))
         WHERE config ? 'sections'
    """)


def downgrade() -> None:
    op.add_column("users", sa.Column("notify_interval_min", sa.Integer(),
                                     server_default="60", nullable=False))
    op.create_check_constraint("notify_interval_bounds", "users",
                               "notify_interval_min BETWEEN 15 AND 1440")
    op.drop_table("platform_settings")
