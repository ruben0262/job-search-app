"""What LinkedIn actually said about applicants, not just the number in it.

Probed against eight live postings: the captions read *"Be among the first 25 applicants"* and
*"Over 200 applicants"*, and the parser returns 25 and 200 — correctly. But **"be among the first
25" means fewer than 25 have applied**, and "over 200" is a ceiling LinkedIn stops counting at. The
card rendered a bare `25`, which reads as twenty-five people having applied — the opposite of what
the page is saying (ROADMAP 6ac).

The number cannot carry that qualifier, so the caption is stored beside it. One nullable text
column, holding what the page said, so the card can render `<25` and `200+` rather than a figure
that means something else.

Revision ID: e7c2b4a91d38
Revises: d5e9a3b81f27
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "e7c2b4a91d38"
down_revision: str | None = "d5e9a3b81f27"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("corpus", sa.Column("applicants_caption", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("corpus", "applicants_caption")
