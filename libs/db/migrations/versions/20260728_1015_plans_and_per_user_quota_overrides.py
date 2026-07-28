"""plans and per-user quota overrides

Revision ID: a913c5f0e742
Revises: f7c2a4e8d165
Create Date: 2026-07-28 10:15:00.000000

One plan at launch (TENANT_SAFETY.md §3). The forward-compat move is not building tiers — it is
keeping the limits in a row rather than in code constants, so adding a paid tier later is an INSERT
rather than a migration plus an audit of every hardcoded number.

`users.plan_id` is added with a server default and backfilled, then the default is kept: every
account has a plan, and an account without one would resolve to no limits at all.
"""
from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'a913c5f0e742'
down_revision: str | None = 'f7c2a4e8d165'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Kept in step with libs.tenant.quotas.FREE_LIMITS by a test, so a fresh database built from
# migrations and one built from the models cannot disagree about what `free` means.
FREE_LIMITS = {
    "search_terms": 25,
    "notifications_per_day": 100,
    "cv_builds_per_day": 10,
    "labels_per_day": 500,
    "claude_spend_per_day_usd": 2.0,
    "proxy_mb_per_day": 200,
    "user_fetch_mb_per_day": 50,
    "corpus_queries_per_min": 60,
}


def upgrade() -> None:
    plans = op.create_table(
        'plans',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('limits', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_plans')),
    )
    op.bulk_insert(plans, [{"id": "free", "name": "Free", "limits": json.dumps(FREE_LIMITS)}])

    op.add_column('users', sa.Column('plan_id', sa.String(length=32), nullable=False,
                                     server_default='free'))
    op.add_column('users', sa.Column('quota_overrides',
                                     postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.create_foreign_key(op.f('fk_users_plan_id_plans'), 'users', 'plans', ['plan_id'], ['id'])


def downgrade() -> None:
    op.drop_constraint(op.f('fk_users_plan_id_plans'), 'users', type_='foreignkey')
    op.drop_column('users', 'quota_overrides')
    op.drop_column('users', 'plan_id')
    op.drop_table('plans')
