"""the alerts and documents migrations of phase 8 joined

Revision ID: ee2add9ee95f
Revises: 24aa78d30e96, f49b1ca9b869
Create Date: 2026-10-29 07:03:07.962217

Two Phase 8 workstreams branched from 60ea03bb2a00 in parallel worktrees: alerts (push subscriptions,
the telegram_connect token purpose) and documents (own AI keys, built documents, the AI allowance).
Neither touches the other's tables, so this only joins them and changes nothing.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'ee2add9ee95f'
down_revision: str | None = ('24aa78d30e96', 'f49b1ca9b869')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
