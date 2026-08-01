"""Per-user ceilings, set by an operator rather than by the person they apply to.

Every cap on the platform was a module constant, which made "give this account more" a deploy. They
are per user now, in one JSONB column, and **the operator owns them** — a user who could raise their
own delivery ceiling is a user who can get their own bot rate-limited by Telegram.

**Absent means the default; `null` means unlimited.** Two different absences, deliberately: a key
that was never set should follow the platform default as that default changes, while an operator who
explicitly chose "no ceiling" should keep it. Storing 0 or a very large number for unlimited would
collapse those two cases and make "unlimited" indistinguishable from "misconfigured".

Revision ID: b2f7c31d9e58
Revises: c9d4b1e6a072
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b2f7c31d9e58"
down_revision: str | None = "c9d4b1e6a072"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("limits", postgresql.JSONB(astext_type=sa.Text()),
                                     nullable=False, server_default=sa.text("'{}'::jsonb")))


def downgrade() -> None:
    op.drop_column("users", "limits")
