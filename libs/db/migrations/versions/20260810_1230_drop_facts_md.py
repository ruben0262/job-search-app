"""A column rendered from a schema on every profile save, that nothing has ever read.

`users.facts_md` was added on 2026-07-28 as the grounding block for generation calls. The day
after, `20260729_1000_profile_context.py` introduced the per-user profile *files* and made
`libs.profiles.grounding` the system prompt instead — and `services/docgen/run.py` has passed that
ever since. The column kept being written and stopped being read on the same day, and nothing
errored, because a column nobody reads never does.

Measured 2026-08-10: `render_facts` had exactly one caller in the product,
`TenantRepository.save_profile`, writing this column; `TenantRepository.refresh_facts`, which
exists to re-render it, had no caller at all. The only readers are two tests asserting the write
happened.

It goes for the reason CLAUDE.md §6 gives: a column is a promise, and one written but never read
is a feature that is silently absent. Nothing is lost — it was derived from `users.profile`, which
is still there.

Revision ID: c81f4a2b6e37
Revises: b73d1e5c9f04
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c81f4a2b6e37"
down_revision = "b73d1e5c9f04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("users", "facts_md")


def downgrade() -> None:
    # Nullable on the way back: it was always derived, and re-rendering it is `save_profile`'s job
    # in whatever revision reinstates a reader for it.
    op.add_column("users", sa.Column("facts_md", sa.Text(), nullable=True))
