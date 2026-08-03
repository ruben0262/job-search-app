"""Search terms an operator injects, to fill the corpus before the users who need it arrive.

The shared corpus only holds what somebody has already searched for, so the second account on the
platform inherits the first one's shape: 240 matching postings out of 14,359, ten labels, and a cold
start with nothing left to buy. Seeding ahead of demand is the only way a new user in a new field
has training data on their first day.

**Deliberately its own table rather than a flag on the user config.** A user's keywords are theirs
and their feed depends on them; an operator must be able to delete their own injected terms and must
never be able to delete somebody's real search. Two kinds of thing that behave differently under
deletion do not belong in one list.

Revision ID: a9c3e5b71d84
Revises: f7b2d9c4e1a3
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "a9c3e5b71d84"
down_revision: str | None = "f7b2d9c4e1a3"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "operator_keywords",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("keyword", sa.String(length=100), nullable=False),
        # Optional: absent means every location the platform already searches, which is the useful
        # default when seeding a field rather than a place.
        sa.Column("location", sa.String(length=64), nullable=True),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.String(length=200), nullable=True),
        # One row per term-and-place. Re-adding is idempotent rather than a duplicate query stem.
        sa.UniqueConstraint("keyword", "location", name=op.f("uq_operator_keywords_term")),
    )


def downgrade() -> None:
    op.drop_table("operator_keywords")
