"""When the request that found a posting went out.

`posted_at → first_seen_at` measures three things added together: how long the board took to
publish, up to fifteen minutes waiting for the next harvest tick, and how long our own fetch took.
Only the third is ours, and it was invisible under a p90 of thirteen hours belonging to the first
(ROADMAP 6ab).

`fetched_at` is the moment the cycle started asking — shared by every link that cycle produced — so
`fetched_at → first_seen_at` isolates our part and `posted_at → fetched_at` isolates theirs.

**Not backfilled.** There is nothing to backfill it from: no existing row records when its request
went out, and inventing a value would produce a confident measurement of nothing. Historical rows
stay NULL and the new latency row reports "not measured yet" until the corpus turns over, exactly as
`embedded_at` did.

Revision ID: d5e9a3b81f27
Revises: c4d8f1a2e7b6
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "d5e9a3b81f27"
down_revision: str | None = "c4d8f1a2e7b6"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("corpus", sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True))
    # **`now()` is the transaction start**, so every row a cycle inserted shared one timestamp taken
    # before the cycle had done any work — which makes `fetched_at → first_seen_at` negative for
    # anything found late in a pass. Same defect the queue had in `finished_at`.
    op.alter_column("corpus", "first_seen_at",
                    server_default=sa.text("clock_timestamp()"))
    # `last_seen_at` moves with it, or the two disagree by however long the transaction had been
    # open — a re-seen row would be stamped as last seen *before* it was first seen.
    op.alter_column("corpus", "last_seen_at",
                    server_default=sa.text("clock_timestamp()"))


def downgrade() -> None:
    op.alter_column("corpus", "last_seen_at", server_default=sa.text("now()"))
    op.alter_column("corpus", "first_seen_at", server_default=sa.text("now()"))
    op.drop_column("corpus", "fetched_at")
