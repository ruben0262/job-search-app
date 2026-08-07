"""A match nobody could send in time is retired, and counted, rather than waiting forever.

**Delivery does not filter.** A posting is judged once, before scoring, and what is scored above the
cutoff is sent — which is right, and leaves one gap: nothing between scoring and sending looks at
age, so if the notify lane stops, the pending queue keeps growing and every row in it keeps ageing.
When the lane comes back it sends all of it, however old.

That is not hypothetical. On 2026-08-07 the delivery-side age check was removed and an 832-row
backlog — every single row more than three days old, some 37 days — began going out as a burst of
19-day-old postings before the lane was paused.

So the queue is maintained rather than filtered: once a minute the notifier's sweep retires any
pending match whose posting has aged past `job_max_age_hours`, sets this column, and logs the count
at WARNING. The send path is untouched and still sends whatever is pending.

`retired_at` rather than `notified_at`: a row we failed to send in time is not a row we delivered,
and writing one as the other would corrupt every delivery count on the platform. This column is the
honest version of the number the roadmap has been calling "expired" — 576 of them the first time
anybody counted.

Revision ID: e5c91a3b7d24
Revises: a7f2e91d4c68
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "e5c91a3b7d24"
down_revision: str | None = "a7f2e91d4c68"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("user_job_state",
                  sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True))
    # The sweep's predicate: pending rows for one user, newest first.
    op.create_index("ix_user_job_state_pending", "user_job_state", ["user_id"],
                    postgresql_where=sa.text("notified_at IS NULL AND retired_at IS NULL"))


def downgrade() -> None:
    op.drop_index("ix_user_job_state_pending", table_name="user_job_state")
    op.drop_column("user_job_state", "retired_at")
