"""rendered rubric on the user row

Revision ID: d8b3e5a91c47
Revises: c41a9f77d3b2
Create Date: 2026-07-27 17:05:00.000000

`profile_version` is `sha256(rubric_md)[:8]`, so keeping the exact bytes that produced the version
is what makes a label's scope auditable after a template change (PROFILE_CAPTURE_PLAN.md §6.1).

A separate revision rather than an edit to c41a9f77d3b2: that one has already been applied, and a
migration is a record of what ran, not a file to revise.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd8b3e5a91c47'
down_revision: str | None = 'c41a9f77d3b2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('users', sa.Column('rubric_md', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'rubric_md')
