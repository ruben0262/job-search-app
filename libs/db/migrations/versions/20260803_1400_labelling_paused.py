"""A stop that actually stops labelling.

Cancelling queued rows was not enough and could never have been: the labeller re-enqueues itself
while work remains, so the batch in flight finished and immediately queued the next one. Stop
deleted a row that was recreated a second later, and the run carried on spending the user's own
credential while the page said it had been stopped.

A flag the worker reads before it re-arms is the only thing that can end a self-perpetuating loop
from outside it.

Revision ID: f7b2d9c4e1a3
Revises: e4a1f0c7d2b9
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "f7b2d9c4e1a3"
down_revision: str | None = "e4a1f0c7d2b9"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("labelling_paused", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("users", "labelling_paused")
