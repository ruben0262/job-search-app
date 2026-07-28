"""onboarding identity and completion

Revision ID: c1a6be40d3f2
Revises: b64d2f31a807
Create Date: 2026-07-28 13:40:00.000000

Page 1 of onboarding (`full_name`, `links`) plus the marker that every page is satisfied.

`onboarding_completed_at` is separate from `users.active` on purpose: finishing onboarding means the
account is configured, while `active` means the scheduler may dispatch notifications — which is only
safe once a model exists to rank with.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c1a6be40d3f2"
down_revision: str | None = "b64d2f31a807"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("full_name", sa.String(length=120), nullable=True))
    op.add_column("users", sa.Column("links", postgresql.JSONB(astext_type=sa.Text()),
                                     nullable=True))
    op.add_column("users", sa.Column("onboarding_completed_at", sa.TIMESTAMP(timezone=True),
                                     nullable=True))


def downgrade() -> None:
    op.drop_column("users", "onboarding_completed_at")
    op.drop_column("users", "links")
    op.drop_column("users", "full_name")
