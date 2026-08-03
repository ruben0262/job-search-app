"""When a vector was written — the clock the middle of the pipeline never had.

`corpus` recorded `description_fetched_at` and then jumped straight to `embedding_version`, so there
was no way to ask when a row became embeddable-and-embedded. That made two of the pipeline's six
handoffs unmeasurable, and both of them hid inside a single found → scored number that could not say
which was slow (ROADMAP 6p).

**Deliberately not backfilled.** The obvious fill — `first_seen_at`, or `description_fetched_at` —
would invent a measurement and hand the latency page a fabricated median for every historical row.
Existing rows stay NULL and the page reports "not measured yet" for those spans until the column
fills, which is a true answer where a backfilled one would not be.

Revision ID: c4d8f1a2e7b6
Revises: a9c3e5b71d84
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "c4d8f1a2e7b6"
down_revision: str | None = "a9c3e5b71d84"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Nullable with no default: a row exists long before it is embedded, and NULL is what
    # "not embedded yet, or embedded before this column existed" honestly looks like.
    op.add_column("corpus", sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("corpus", "embedded_at")
