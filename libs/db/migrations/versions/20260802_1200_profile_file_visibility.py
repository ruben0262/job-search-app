"""Per-user permission to read the profile files written about them.

**Default false, which is a deliberate change of posture.** These files hold an assessment of a
person -- what they are good at, what a role would make of them -- written by an agent and not yet
reviewed by anybody. Showing that to its subject by default, while the wording is still being
settled, risks somebody reading half a sentence about themselves out of context. The operator opens
it per account once they are content with what it says.

Per user rather than platform-wide because the decision is about a particular person's files and a
particular conversation: an account whose setup has been reviewed can be opened without opening
everyone's.

Revision ID: e4a1f0c7d2b9
Revises: b2f7c31d9e58
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "e4a1f0c7d2b9"
down_revision: str | None = "b2f7c31d9e58"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("profile_files_visible", sa.Boolean(), nullable=False,
                  server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("users", "profile_files_visible")
