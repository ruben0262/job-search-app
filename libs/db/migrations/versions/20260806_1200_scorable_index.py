"""The index the pre-score filter reads: newest first, open, embedded.

**Measured before and after, and after says "no change today"** (CLAUDE.md §4). The score lane's
candidate query plans a sequential scan over `corpus` at ~62 ms on 29,000 rows, and it plans the same
scan with this index present. Adding it changed 64 ms to 59 ms, which is noise.

That is not an argument against the index, but the argument for it is not the one first written
here. A scan is chosen because the predicate selects 8% of the table, and 8% is past the crossover
where an index that must still visit the heap beats reading it straight through. **Selectivity does
not improve because a table gets bigger — but this predicate's does**, because the binding condition
is a 24-hour freshness window while the corpus grows without bound. At the 400,000 rows the
architecture projects, one day of intake is still roughly 2,700 rows: 0.7% rather than 8%, which is
firmly index territory.

So it is retained on a projection with its mechanism stated, and it earns nothing yet. If the
freshness window is ever widened enough to select a large fraction again, this index stops being
used and should be dropped rather than left as decoration.

Partial on the two conditions every scoring query carries and neither of which changes per user: a
row with no embedding cannot be scored, and a closed posting has nothing to apply for. Ordered so
the `ORDER BY first_seen_at DESC LIMIT 500` can be served without a sort.

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
