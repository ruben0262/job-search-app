"""An operator flag that lives in the database rather than only in an env var.

`ADMIN_EMAILS` stays and stays authoritative — it is the bootstrap, and it is what an operator can
still use when the database is the thing that is broken. What it cannot do is be granted to somebody
without a container restart, which makes "give this person access" a deploy.

So the two are read together: an account is an operator if its address is in the env var **or** its
row says so. That direction matters — a flag cannot revoke the bootstrap, so an operator cannot lock
themselves out of their own platform by toggling a switch in a browser.

Revision ID: c9d4b1e6a072
Revises: a1c8e5f20b74
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "c9d4b1e6a072"
down_revision: str | None = "a1c8e5f20b74"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("is_admin", sa.Boolean(), nullable=False,
                                     server_default="false"))


def downgrade() -> None:
    op.drop_column("users", "is_admin")
