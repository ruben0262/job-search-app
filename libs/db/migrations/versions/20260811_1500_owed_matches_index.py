"""an index for "does this account have anything owed"

Revision ID: e91c47d3b285
Revises: d5f83a17c604
Create Date: 2026-08-11 15:00:00.000000

The health check asks this once per account, every fifteen minutes, and it was answered by a
sequential scan of the whole of `user_job_state`. `ix_user_job_state_pending` cannot serve it: on the
live platform 95,203 of 101,903 rows satisfy `notified_at IS NULL AND retired_at IS NULL`, so it
selects 93% of the table and the planner ignores it, correctly.

`score` is the selective column. 30,442 of those rows are `score_lane.skip()` tombstones with no
score at all, and ordering the rest by score turns "anything at or above this account's cutoff" into
a range seek that stops at the first miss — which is the common answer, and the expensive one to
prove by scanning.

Additive and safe to apply while the platform runs (ROADMAP 6be).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'e91c47d3b285'
down_revision: str | None = 'd5f83a17c604'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        'ix_ujs_owed', 'user_job_state', ['user_id', 'score'], unique=False,
        postgresql_where=sa.text(
            'notified_at IS NULL AND retired_at IS NULL AND score IS NOT NULL'))


def downgrade() -> None:
    op.drop_index(
        'ix_ujs_owed', table_name='user_job_state',
        postgresql_where=sa.text(
            'notified_at IS NULL AND retired_at IS NULL AND score IS NOT NULL'))
