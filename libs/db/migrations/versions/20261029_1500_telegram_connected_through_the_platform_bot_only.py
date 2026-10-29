"""a Telegram chat counts as connected only once it was linked through the platform's bot

Revision ID: 3c7d1e9a5b20
Revises: ee2add9ee95f
Create Date: 2026-10-29 15:00:00.000000

ROADMAP Phase 8. Accounts connected before 2026-10-28 used bots of their own, and kept
`telegram_status = 'connected'` when delivery moved to one platform bot. That bot has never been
started in their chats, so their first delivery would be refused, the account marked `broken`, and the
person told they had blocked a bot they never saw. Moving them to `unconfigured` shows them the one-tap
"Connect Telegram" instead. Only accounts with no redeemed `telegram_connect` code move; their stored
chat id stays, and pressing Start in the platform bot replaces it. Nothing to undo: which accounts were
connected the old way is not recoverable, and none of them can be reached until they press Start.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "3c7d1e9a5b20"
down_revision: str | None = "ee2add9ee95f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Module-level so `tests/test_alerts.py` runs exactly the statement this migration runs.
LEGACY_TELEGRAM = """
    UPDATE users SET telegram_status = 'unconfigured'
    WHERE telegram_status IN ('connected', 'broken')
      AND NOT EXISTS (SELECT 1 FROM auth_tokens t
                      WHERE t.user_id = users.id AND t.purpose = 'telegram_connect'
                        AND t.used_at IS NOT NULL)
"""


def upgrade() -> None:
    op.execute(LEGACY_TELEGRAM)


def downgrade() -> None:
    pass
