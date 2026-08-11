"""drop corpus.pinned and corpus.trimmed_at — retention archives the row instead

Revision ID: d5f83a17c604
Revises: a4e7c92f1b53
Create Date: 2026-08-11 12:00:00.000000

`trim_cold` nulled a row's description and vector in place and stamped `trimmed_at`. That is data
loss with none of the visible signs of it: the trainer rebuilds every training set by joining
`user_labels` to the corpus and reading the embedding, so a trimmed row left the next retrain
silently. Retention now writes the whole row to an archive file and deletes it
(`libs.corpus.archive`).

`pinned` went with it. It was a denormalised copy of "somebody labelled or acted on this", and two
of the three places that write a label never set it — so most of every training set sat on rows the
retention code considered disposable. The archive asks `user_labels` and `user_job_state` directly.

**Destructive, and in one direction only**: `trimmed_at` values cannot be reconstructed on downgrade.
Nothing had ever been trimmed on any box (0 rows, checked 2026-08-10) so nothing is lost in practice,
and the downgrade recreates both columns empty.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd5f83a17c604'
down_revision: str | None = 'a4e7c92f1b53'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The partial index existed to find trim candidates by the two columns being dropped.
    op.drop_index('ix_corpus_trim_candidates', table_name='corpus',
                  postgresql_where=sa.text('pinned = false AND trimmed_at IS NULL'))
    # The archive reads oldest-first with no flag in the predicate, so the replacement is plain.
    op.create_index('ix_corpus_first_seen', 'corpus', ['first_seen_at'], unique=False)
    op.drop_column('corpus', 'pinned')
    op.drop_column('corpus', 'trimmed_at')
    # Retention asks "does any account have a label on this posting", which is a lookup by `job_id`
    # alone — and `job_id` is the second column of the primary key, so the PK cannot serve it.
    # Measured on 50,000 postings and 2,000 labels: 21.2 ms without, 10.6 ms with.
    op.create_index('ix_user_labels_job', 'user_labels', ['job_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_user_labels_job', table_name='user_labels')
    op.add_column('corpus', sa.Column('trimmed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('corpus', sa.Column('pinned', sa.Boolean(), nullable=False,
                                      server_default=sa.false()))
    op.drop_index('ix_corpus_first_seen', table_name='corpus')
    op.create_index('ix_corpus_trim_candidates', 'corpus', ['first_seen_at'], unique=False,
                    postgresql_where=sa.text('pinned = false AND trimmed_at IS NULL'))
