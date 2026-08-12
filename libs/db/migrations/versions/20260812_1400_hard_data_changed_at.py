"""when the user last changed something they typed themselves

Revision ID: c7a1f2e94d80
Revises: b2c4e8f60a19
Create Date: 2026-08-12 14:00:00.000000

The profile files are written by the setup agents from the user's documents and from what they typed.
Nothing noticed when the typed half changed afterwards: one account added two links under Your
details after setup had finished, and the prompts that carry their contact line were never told.

Stamped by the two routes that save typed data, and compared against each profile file's own
`updated_at` to find the files written before it. A timestamp rather than a flag because a flag has to
be cleared, and a clear that never happens is a permanent banner while one that happens too early
leaves the files stale with nothing saying so.

Backfilled to now for existing accounts rather than left NULL: NULL means "never changed anything",
which for an account that has been editing its settings for weeks is a claim this migration cannot
make. Setting it to now marks their files as possibly-stale once, which is true and is the safe
direction — the worst case is one refresh nobody needed.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c7a1f2e94d80'
down_revision: str | None = 'b2c4e8f60a19'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users",
                  sa.Column("hard_data_changed_at", sa.TIMESTAMP(timezone=True), nullable=True))
    # Only accounts that have actually typed something: a signup with no name and no config has
    # nothing that could have gone stale.
    op.execute("""
        UPDATE users SET hard_data_changed_at = now()
         WHERE full_name IS NOT NULL OR links IS NOT NULL
    """)


def downgrade() -> None:
    op.drop_column("users", "hard_data_changed_at")
