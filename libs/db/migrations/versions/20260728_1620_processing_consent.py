"""processing consent

Revision ID: e5c9b71a4d38
Revises: d3f8a1c25b90
Create Date: 2026-07-28 16:20:00.000000

Consent to send document text to Anthropic, recorded at the moment it is owed.

**The moment moved.** The obligation was written when capture uploaded files to the Anthropic Files
API, and it named *upload* as the point of consent. Files no longer leave the platform — text is
extracted server-side — so nothing crosses a processor boundary until the user presses *process
data*. Asking at upload would have been asking about something that had not happened yet, and
worse, it would have made the answer stale by the time it mattered.

Nullable, with no backfill. An existing account has not consented; it is asked the next time it
processes. Defaulting it to "now" would be recording a consent nobody gave, which is the one thing
this column exists to prevent.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5c9b71a4d38"
down_revision: str | None = "d3f8a1c25b90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("processing_consent_at", sa.TIMESTAMP(timezone=True),
                                     nullable=True))
    # Which version of the disclosure they agreed to. A policy that names a new processor is a new
    # disclosure, and "they consented" is not an answer to "to what".
    op.add_column("users", sa.Column("processing_consent_version", sa.String(length=16),
                                     nullable=True))


def downgrade() -> None:
    op.drop_column("users", "processing_consent_version")
    op.drop_column("users", "processing_consent_at")
