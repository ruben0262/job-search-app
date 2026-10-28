"""the green and red words suggested to each person

Revision ID: 60ea03bb2a00
Revises: c7a1f2e94d80
Create Date: 2026-10-28 16:00:00.000000

ROADMAP Phase 8, the profile workstream. Words worked out by rules from a person's own documents and
the corpus (`libs.suggestions`), each with its reason, plus the ones they dismissed so they are never
offered again. One JSONB column rather than a table: they are read whole, written whole, and belong
to one account.

Left NULL for existing accounts. NULL means "never worked out", and the first read works them out,
so nobody waits for an upload to see any.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = '60ea03bb2a00'
down_revision: str | None = 'c7a1f2e94d80'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("suggestions", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("users", "suggestions")
