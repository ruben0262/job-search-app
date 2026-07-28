"""score lane

Revision ID: a7e3f9c14b62
Revises: f1d84c2e9b07
Create Date: 2026-07-28 17:00:00.000000

The lane that turns a harvested row into a score.

Until this existed nothing did: the harvest cycle stored and embedded, the notify worker delivered
what was already scored, and the step between them was a manual script. Both live accounts were 398
rows behind and growing at the harvest rate.

A lane rather than a call from the notifier because `services.notifier` may not import
`services.scorer` — and that contract is load-bearing here, not ceremonial: the scorer's fail-closed
version checks belong in the deployable that owns them.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a7e3f9c14b62"
down_revision: str | None = "f1d84c2e9b07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "lane IN ('notify', 'train', 'docgen', 'label', 'process')"
_NEW = "lane IN ('notify', 'train', 'docgen', 'label', 'process', 'score')"

# The bare name: `libs.db.base` sets a naming convention and Alembic applies it to whatever is
# passed, so a rendered name would be double-prefixed.
_CONSTRAINT = "lane_valid"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "jobs", type_="check")
    op.create_check_constraint(_CONSTRAINT, "jobs", _NEW)


def downgrade() -> None:
    op.execute("DELETE FROM jobs WHERE lane = 'score'")
    op.drop_constraint(_CONSTRAINT, "jobs", type_="check")
    op.create_check_constraint(_CONSTRAINT, "jobs", _OLD)
