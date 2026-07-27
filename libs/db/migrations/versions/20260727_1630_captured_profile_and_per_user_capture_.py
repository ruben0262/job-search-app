"""captured profile and per-user capture agent id

Revision ID: c41a9f77d3b2
Revises: b072fd4c1edb
Create Date: 2026-07-27 16:30:00.000000

`users.profile` holds the confirmed capture output (PROFILE_CAPTURE_PLAN.md §5) — the single source
of truth every rendered artifact derives from. `capture_agent_id` is the Managed Agents agent
object, which is created with the user's own key and therefore lives in their workspace: one per
user, reused on re-capture rather than recreated per session.

Both nullable: existing accounts have no profile, and the migrated reference account is configured
without one.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'c41a9f77d3b2'
down_revision: str | None = 'b072fd4c1edb'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('users', sa.Column('profile', postgresql.JSONB(astext_type=sa.Text()),
                                     nullable=True))
    # Added WITH a server default so existing rows get a value (Postgres will not add a NOT NULL
    # column without one), then dropped again so the schema matches the model — `env.py` runs with
    # `compare_server_default=True`, and a default left behind here would fail the drift check on
    # every subsequent commit.
    op.add_column('users', sa.Column('profile_schema_version', sa.SmallInteger(),
                                     nullable=False, server_default='1'))
    op.alter_column('users', 'profile_schema_version', server_default=None)
    op.add_column('users', sa.Column('capture_agent_id', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'capture_agent_id')
    op.drop_column('users', 'profile_schema_version')
    op.drop_column('users', 'profile')
