"""A turn the user can stop.

A setup turn runs on a worker for minutes on the user's own credential, and until now there was no
way to end one. Leaving the page does not stop it — that is deliberate, it is what makes a reply
survive a closed tab — so a user who realises they asked the wrong thing could only watch it finish
and pay for it.

Two new states on `setup_messages.status`:

- **`cancelling`** — the user has asked to stop and the worker has not noticed yet. Set by the API,
  cleared by the worker. It exists because the worker is mid-HTTP-call to Anthropic when the request
  arrives and cannot be interrupted there; it checks between tool steps, which is the only safe
  seam. Without a distinct state the API would have to either block until the worker noticed, or
  lie about having stopped something still running.
- **`cancelled`** — the worker stopped and committed what it had. A finished state, and deliberately
  **not** `failed`: nothing went wrong, and showing a user's own decision as an error is both untrue
  and the kind of thing that generates a support message.

The constraint is replaced rather than dropped. A status column with no constraint accepts a typo
forever, and this one already drives rendering.

Revision ID: f3a71c2d4e90
Revises: e6b2c94af518
"""
from __future__ import annotations

from alembic import op

revision: str = "f3a71c2d4e90"
down_revision: str | None = "e6b2c94af518"
branch_labels: str | None = None
depends_on: str | None = None

_OLD = "status IN ('pending', 'streaming', 'done', 'failed')"
_NEW = "status IN ('pending', 'streaming', 'done', 'failed', 'cancelling', 'cancelled')"


def upgrade() -> None:
    op.drop_constraint("status", "setup_messages", type_="check")
    op.create_check_constraint("status", "setup_messages", _NEW)


def downgrade() -> None:
    # Rows in a state the old constraint forbids would make it un-creatable, so they are settled
    # first: a cancelled turn is a finished one, and a cancelling turn was never going to survive
    # a downgrade anyway.
    op.execute("UPDATE setup_messages SET status = 'failed', "
               "error = coalesce(error, 'stopped') WHERE status IN ('cancelling', 'cancelled')")
    op.drop_constraint("status", "setup_messages", type_="check")
    op.create_check_constraint("status", "setup_messages", _OLD)
