"""auth sessions and email tokens

Revision ID: e5f1c0a7b284
Revises: d8b3e5a91c47
Create Date: 2026-07-27 20:10:00.000000

Both tables store hashes only (AUTH_REUSE.md §3). A database dump must not let anyone resume a
session or verify an address — the property libs/secrets gives credentials, applied to the things
that grant access to them.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'e5f1c0a7b284'
down_revision: str | None = 'd8b3e5a91c47'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'user_sessions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('refresh_hash', sa.String(length=64), nullable=False),
        sa.Column('expires_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('rotated_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('ip', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_sessions_user_id_users'),
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_user_sessions')),
        sa.UniqueConstraint('refresh_hash', name=op.f('uq_user_sessions_refresh_hash')),
    )
    op.create_index('ix_user_sessions_live', 'user_sessions', ['user_id'],
                    postgresql_where=sa.text('revoked_at IS NULL AND rotated_at IS NULL'))

    op.create_table(
        'auth_tokens',
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('purpose', sa.String(length=32), nullable=False),
        sa.Column('expires_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('used_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.CheckConstraint("purpose IN ('verify_email', 'reset_password')",
                           name=op.f('ck_auth_tokens_purpose_valid')),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_auth_tokens_user_id_users'),
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('token_hash', name=op.f('pk_auth_tokens')),
    )
    op.create_index('ix_auth_tokens_outstanding', 'auth_tokens', ['user_id', 'purpose'])


def downgrade() -> None:
    op.drop_index('ix_auth_tokens_outstanding', table_name='auth_tokens')
    op.drop_table('auth_tokens')
    op.drop_index('ix_user_sessions_live', table_name='user_sessions')
    op.drop_table('user_sessions')
