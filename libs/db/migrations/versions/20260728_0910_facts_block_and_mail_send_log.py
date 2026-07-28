"""facts block and mail send log

Revision ID: f7c2a4e8d165
Revises: e5f1c0a7b284
Create Date: 2026-07-28 09:10:00.000000

`users.facts_md` is the generation-grounding block (PROFILE_CAPTURE_PLAN.md §6.2) — the CLAUDE.md
equivalent for a service with no CLI to auto-load one.

`mail_sends` counts outbound messages against the platform's daily allowance. It deliberately holds
no recipient address: a cap needs a count, not a mailing list.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'f7c2a4e8d165'
down_revision: str | None = 'e5f1c0a7b284'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('users', sa.Column('facts_md', sa.Text(), nullable=True))
    op.create_table(
        'mail_sends',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('purpose', sa.String(length=32), nullable=False),
        sa.Column('sent_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.CheckConstraint("purpose IN ('verify_email', 'reset_password', 'notice')",
                           name=op.f('ck_mail_sends_purpose_valid')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_mail_sends')),
    )
    op.create_index('ix_mail_sends_recent', 'mail_sends', ['sent_at'])


def downgrade() -> None:
    op.drop_index('ix_mail_sends_recent', table_name='mail_sends')
    op.drop_table('mail_sends')
    op.drop_column('users', 'facts_md')
