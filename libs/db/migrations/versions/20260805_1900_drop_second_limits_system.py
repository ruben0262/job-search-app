"""Drop the second per-user limits system, and a column whose feature lives elsewhere.

**Two mechanisms answered "what is this account allowed to do", and two keys existed in both.**
`libs.limits` reads `users.limits` and is what the admin panel writes; the plan system read
`users.quota_overrides` merged over `plans.limits`, and `notifications_per_day` and
`proxy_mb_per_day` were defined in each. The plan system enforced exactly one limit in the whole
tree — `cv_builds_per_day`, at the Telegram build button — and **nothing ever wrote the override
column**, so the per-user exception it existed to grant could not be granted (ROADMAP 6as).

`cv_builds_per_day` has moved into the catalogue with a control in the panel. `plans` and
`users.plan_id` stay: the plan is a label the account and admin pages display, and it is where a
paid tier will hang. What goes is the parallel limits mechanism.

`users.profile_context` goes with them for a different reason. It was declared for the grounding
prose that shapes a generated CV — and that feature exists and works as `context.md` in
`user_profile_files`, written by the setup chat and required by `libs.profiles.grounding`. The
column was the earlier design of the same idea; it was never written and never read.

Both are dropped rather than left: a column nothing writes reads as NULL forever, which is
indistinguishable from a feature that is simply missing.

Revision ID: b8e5d3f71c04
Revises: a1f4c8d29e63
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "b8e5d3f71c04"
down_revision: str | None = "a1f4c8d29e63"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_column("users", "quota_overrides")
    op.drop_column("users", "profile_context")


def downgrade() -> None:
    op.add_column("users", sa.Column("profile_context", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("quota_overrides",
                                     sa.dialects.postgresql.JSONB(), nullable=True))
