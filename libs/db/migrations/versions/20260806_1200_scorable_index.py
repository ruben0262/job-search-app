"""The index the pre-score filter reads: newest first, open, embedded.

**Measured before it was added** (CLAUDE.md §4). The score lane's candidate query — not scored at
this model version, open, inside the account's freshness window, in one of their countries, newest
first — planned a **sequential scan over `corpus`** at 64 ms on 29,000 rows. That is survivable
today and is not at the 400,000 the architecture projects, where the same scan is roughly a second
per batch *per account*.

Partial on the two conditions every scoring query carries and neither of which changes per user:
a row with no embedding cannot be scored, and a closed posting has nothing to apply for. That keeps
the index to the rows that are actually candidates rather than to the whole table, and it is ordered
so the `ORDER BY first_seen_at DESC LIMIT 500` is served by the index rather than by a sort.

Revision ID: c9d2e18a4f75
Revises: b8e5d3f71c04
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "c9d2e18a4f75"
down_revision: str | None = "b8e5d3f71c04"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_index(
        "ix_corpus_scorable", "corpus", [sa.text("first_seen_at DESC")],
        postgresql_where=sa.text("closed_at IS NULL AND embedding IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("ix_corpus_scorable", table_name="corpus")
