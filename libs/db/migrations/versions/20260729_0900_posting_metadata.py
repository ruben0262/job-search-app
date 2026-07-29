"""posting metadata the feed card reads

Revision ID: c4d1a7b93e58
Revises: b8e2d05f713c
Create Date: 2026-07-29 09:00:00.000000

**The four things the reference feed card shows and this corpus could not answer.**

`watcher/feed.py` renders how contested a posting is (👥 applicant count), whether it is one-click
(⚡ easy apply), whether it is a repost (♻️), and its workplace mode. The platform's harvester
collected `easy_apply` on the `JobLink` and dropped it in `as_corpus_row`; the other three were
never modelled. The result was a three-line card carrying a title and a URL — a match with none of
the context that decides whether it is worth ten minutes.

All nullable, all opportunistic. A source that does not report applicant counts is not a reason to
withhold a match, so the card omits the element rather than the job.

`posted_at` already existed and was never written: `JobLink.date_posted` stopped at
`as_corpus_row()` too, which is why every card so far has had no age on it. Nothing to migrate for
that one — it is a bug in the harvester, fixed in the same change.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4d1a7b93e58"
down_revision: str | None = "b8e2d05f713c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # LinkedIn's own caption, parsed to an integer. "Over 200 applicants" lands as 200, which is
    # also the point at which the reference treats a listing as heavily over-subscribed.
    op.add_column("corpus", sa.Column("applicants", sa.Integer(), nullable=True))
    # True = one click on the board, False = an external apply link. The false case is what makes
    # the apply-URL dedup layer resolvable, so it is worth storing even where the card ignores it.
    op.add_column("corpus", sa.Column("easy_apply", sa.Boolean(), nullable=True))
    op.add_column("corpus", sa.Column("reposted", sa.Boolean(), nullable=True))
    # 'remote' | 'hybrid' | 'onsite'. Nullable means unknown, and unknown means keep — the same
    # fail-open rule `libs/corpus/workfilter.py` applies.
    op.add_column("corpus", sa.Column("workplace", sa.String(length=8), nullable=True))
    op.create_check_constraint(
        "ck_corpus_workplace_valid", "corpus",
        "workplace IS NULL OR workplace IN ('remote', 'hybrid', 'onsite')")


def downgrade() -> None:
    op.drop_constraint("ck_corpus_workplace_valid", "corpus", type_="check")
    for column in ("workplace", "reposted", "easy_apply", "applicants"):
        op.drop_column("corpus", column)
