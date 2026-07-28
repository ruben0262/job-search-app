"""process lane and the profile draft

Revision ID: d3f8a1c25b90
Revises: c1a6be40d3f2
Create Date: 2026-07-28 14:50:00.000000

*Process data* gets its own lane rather than sharing one. It is a single long Opus call on the
user's own credential, and the two properties that matter are per-lane: a visibility timeout long
enough that a sweeper cannot reclaim a running job and charge the user twice, and a worker pool that
cannot be starved by a burst of relabelling.

`profile_draft` is separate from `profile` because applying a profile bumps `profile_generation` and
invalidates every label the user has. Doing that on the strength of text nobody has read yet is
exactly what `PROFILE_CAPTURE_PLAN.md` §5 forbids.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d3f8a1c25b90"
down_revision: str | None = "c1a6be40d3f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_LANES = "lane IN ('notify', 'train', 'docgen', 'label')"
_NEW_LANES = "lane IN ('notify', 'train', 'docgen', 'label', 'process')"

# The bare name, not the rendered one. `libs.db.base` sets a naming convention of
# `ck_%(table_name)s_%(constraint_name)s`, and Alembic applies it to what is passed here — so
# passing the already-rendered `ck_jobs_lane_valid` asks the database for
# `ck_jobs_ck_jobs_lane_valid`, which does not exist.
_CONSTRAINT = "lane_valid"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "jobs", type_="check")
    op.create_check_constraint(_CONSTRAINT, "jobs", _NEW_LANES)

    op.add_column("users", sa.Column("profile_draft", postgresql.JSONB(astext_type=sa.Text()),
                                     nullable=True))
    op.add_column("users", sa.Column("capture_source", sa.String(length=16), nullable=True))
    op.add_column("users", sa.Column("documents_changed_at", sa.TIMESTAMP(timezone=True),
                                     nullable=True))
    op.add_column("users", sa.Column("processed_at", sa.TIMESTAMP(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "processed_at")
    op.drop_column("users", "documents_changed_at")
    op.drop_column("users", "capture_source")
    op.drop_column("users", "profile_draft")

    # Any `process` job would violate the old constraint. Deleting them is correct: the lane no
    # longer has a worker, so they would sit `ready` forever.
    op.execute("DELETE FROM jobs WHERE lane = 'process'")
    op.drop_constraint(_CONSTRAINT, "jobs", type_="check")
    op.create_check_constraint(_CONSTRAINT, "jobs", _OLD_LANES)
