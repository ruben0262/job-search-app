"""shadow scores — the judge's opinion beside the model's, on a 10% sample

Revision ID: bf6f053be9f5
Revises: f8b3d61ca204
Create Date: 2026-07-29 16:10:50.607357

Purely additive: a new table nothing reads yet. Deploying it before the code that writes it is the
safe order, and an empty table changes no behaviour.

The index is the one query this exists to serve — "how well has the model agreed with the judge
lately, for this user" — which is a scan of recent rows for one account. Without it, the drift
check on a mature account walks every pair ever collected.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'bf6f053be9f5'
down_revision: str | None = 'f8b3d61ca204'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('shadow_scores',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('job_id', sa.BigInteger(), nullable=False),
    sa.Column('model_score', sa.Float(), nullable=True),
    sa.Column('judge_score', sa.Float(), nullable=True),
    sa.Column('agreed', sa.Boolean(), nullable=True),
    sa.Column('rationale', sa.String(length=500), nullable=True),
    sa.Column('model_version', sa.Integer(), nullable=True),
    sa.Column('profile_generation', sa.SmallInteger(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['corpus.id'], name=op.f('fk_shadow_scores_job_id_corpus'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_shadow_scores_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'job_id', name=op.f('pk_shadow_scores'))
    )
    op.create_index('ix_shadow_scores_user_recent', 'shadow_scores',
                    ['user_id', sa.text('created_at DESC')])


def downgrade() -> None:
    op.drop_index('ix_shadow_scores_user_recent', table_name='shadow_scores')
    op.drop_table('shadow_scores')
