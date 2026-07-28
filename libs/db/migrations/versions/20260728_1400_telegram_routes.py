"""telegram routes

Revision ID: f1d84c2e9b07
Revises: e5c9b71a4d38
Create Date: 2026-07-28 14:00:00.000000

Optional per-category Telegram channels, carried from the reference instance where the owner splits
their feed into Hungarian jobs, LinkedIn, and everything else.

JSONB rather than three columns: the set of categories is a product question that will change, and
a new one should not be a migration. Nothing queries by route — it is read whole, per delivery.

Nullable with no backfill. An account without it delivers everything to its personal chat, which is
exactly what every account does today.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f1d84c2e9b07"
down_revision: str | None = "e5c9b71a4d38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("telegram_routes", postgresql.JSONB(astext_type=sa.Text()),
                                     nullable=True))


def downgrade() -> None:
    op.drop_column("users", "telegram_routes")
