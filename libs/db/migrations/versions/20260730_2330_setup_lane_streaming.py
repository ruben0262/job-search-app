"""the setup lane, and a turn you can watch

Revision ID: e6b2c94af518
Revises: d1f4a83b6027
Create Date: 2026-07-30 23:30:00.000000

**A chat turn was an HTTP request that took minutes, so navigating away killed it.** The user's
message was already committed and the reply never arrived — the conversation lost its answer and
nothing said so. Closing a tab, switching chats, refreshing or signing out all did it.

So a turn becomes a queue job like every other slow thing on this platform. The assistant's row is
created **pending, up front**, and the worker streams text into it as the model produces it: the
page has something to render from the first token, and the row is the same row whether you watched
it arrive or came back to it tomorrow.

`status` is on the message rather than tracked in the queue because the transcript is what the page
reads. A pending row *is* the "thinking" state, which means there is nothing extra to reconcile
when a worker dies mid-turn — the sweep marks it failed and the reader sees why.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e6b2c94af518"
down_revision: str | None = "d1f4a83b6027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The bare name: `libs.db.base` sets a naming convention and Alembic applies it to whatever is
# passed, so a rendered name would be double-prefixed (the same trap the score-lane migration
# documents).
_CONSTRAINT = "lane_valid"
_OLD = "lane IN ('notify', 'train', 'docgen', 'label', 'process', 'score')"
_NEW = "lane IN ('notify', 'train', 'docgen', 'label', 'process', 'score', 'setup')"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "jobs", type_="check")
    op.create_check_constraint(_CONSTRAINT, "jobs", _NEW)

    # Existing rows are all finished turns: they were written by the synchronous path, which only
    # ever committed a reply it already had.
    op.add_column("setup_messages",
                  sa.Column("status", sa.String(length=16), nullable=False,
                            server_default="done"))
    # Why a turn failed, in the words the reader gets. Null unless `status = 'failed'`.
    op.add_column("setup_messages", sa.Column("error", sa.Text(), nullable=True))
    op.create_check_constraint(
        "status", "setup_messages",
        "status IN ('pending', 'streaming', 'done', 'failed')")


def downgrade() -> None:
    op.drop_constraint("status", "setup_messages", type_="check")
    op.drop_column("setup_messages", "error")
    op.drop_column("setup_messages", "status")
    op.drop_constraint(_CONSTRAINT, "jobs", type_="check")
    op.create_check_constraint(_CONSTRAINT, "jobs", _OLD)
