"""Delivery has no cadence, so it has no clock and no next slot.

**A scored match is sent because it exists.** The score lane enqueues the notify job in the same
transaction that records the score, a run that fills its limit queues its own continuation, and a
sweep once a minute catches anything neither of those queued. There is nothing left to be "due".

So `users.next_run_at` and its index go, along with the `delivery_interval_min` platform setting.
The column was written by the dispatcher and read by the same query, and with the dispatcher gone it
is a promise nothing keeps — the shape of defect this project has shipped most often, and the reason
it goes in the same commit as the mechanism it belonged to rather than being left "in case".

`linkedin_next_run_at` stays. That tier is per-user, spends the user's own bandwidth and carries ban
risk, so it is deliberately paced and its clock is real (`services.harvester.governor`).

Revision ID: a7f2e91d4c68
Revises: d4b8f1c37a92
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "a7f2e91d4c68"
down_revision: str | None = "d4b8f1c37a92"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_index("ix_users_due", table_name="users")
    op.drop_column("users", "next_run_at")
    op.execute("DELETE FROM platform_settings WHERE key = 'delivery_interval_min'")


def downgrade() -> None:
    op.add_column("users", sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_users_due", "users", ["next_run_at"],
                    postgresql_where=sa.text("active"))
