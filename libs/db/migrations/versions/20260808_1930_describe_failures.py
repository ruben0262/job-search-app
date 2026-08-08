"""A posting we could not read twice is abandoned, and never fetched again.

The describer had no memory. A row whose page returns no usable text came back in the very next
pass, and the one after, forever: measured on 2026-08-08, the same ~56 LinkedIn ids every fifteen
minutes, the oldest first seen 167 hours earlier. On the proxied tier every one of those attempts
is a user's own metered residential bandwidth, spent on a posting that had already refused to
answer.

Bounding it by age (the previous migration's sibling change) cut the backlog from 213 to 56 but
does not stop the waste inside the window: a fresh posting that fails still gets retried on every
pass until it ages out, which at a 15-minute tick and a 24-hour window is 96 attempts.

So the row counts its own failures. Two consecutive misses and it is abandoned — dropped from
describing, from embedding and from scoring, because a posting whose text we never got is a
posting whose only description is its own title, and embedding that produces a vector for a
sentence rather than for a job.

Two, not three: the second attempt is already the evidence. The first can be a rate limit or a
blip; a second failure on a fresh IP says the page has nothing to give.

`smallint NOT NULL DEFAULT 0` and backfilled to 0: every existing row starts with a clean slate,
so the rule applies from its first failure after this deploys rather than retroactively condemning
rows on a history nobody recorded.

Revision ID: b73d1e5c9f04
Revises: f2a91c47b6d8
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "b73d1e5c9f04"
down_revision: str | None = "f2a91c47b6d8"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("corpus", sa.Column("describe_failures", sa.SmallInteger(), nullable=False,
                                      server_default="0"))
    # Partial: the interesting rows are the few that have failed, not the millions that have not.
    op.create_index(op.f("ix_corpus_describe_failures"), "corpus", ["describe_failures"],
                    postgresql_where=sa.text("describe_failures > 0"))


def downgrade() -> None:
    op.drop_index(op.f("ix_corpus_describe_failures"), table_name="corpus")
    op.drop_column("corpus", "describe_failures")
