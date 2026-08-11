"""drop the dormant account status

Revision ID: a4e7c92f1b53
Revises: c81f4a2b6e37
Create Date: 2026-08-11 11:00:00.000000

`dormant` was a state that `mark_dormant` wrote, nothing called, and the login path un-set
incompletely: it restored `status` to 'active' and left `active` False, which is an account that
looks live and puts no keywords into the harvest query set. Removed rather than repaired — an
operator reads `last_active_at` in the accounts list and deletes the account if they want it gone
(ROADMAP 6c).

Alembic autogenerate does NOT detect CHECK constraint changes, which is how `82e1ca386c02` shipped a
constraint listing the wrong values while the drift check passed. Written explicitly here for the
same reason, and `libs/db/README.md` records the limitation.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'a4e7c92f1b53'
down_revision: str | None = 'c81f4a2b6e37'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nothing should hold this status: the only writer never ran on any box. Converted rather than
    # asserted, because a migration that fails on the constraint leaves the deploy half-applied and
    # an account stuck dormant is one that harvests nothing until somebody notices.
    op.execute("UPDATE users SET status = 'active', active = true WHERE status = 'dormant'")
    op.drop_constraint("status_valid", "users", type_="check")
    op.create_check_constraint(
        "status_valid", "users",
        "status IN ('pending_verification', 'active', 'suspended', 'deleted')")


def downgrade() -> None:
    op.drop_constraint("status_valid", "users", type_="check")
    op.create_check_constraint(
        "status_valid", "users",
        "status IN ('pending_verification', 'active', 'dormant', 'suspended', 'deleted')")
