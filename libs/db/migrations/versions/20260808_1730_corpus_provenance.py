"""How a posting got into the corpus, recorded rather than reconstructed.

On 2026-08-08 an account with no proxy was receiving LinkedIn mechanical-engineering postings, and
answering "how did those get here" took an afternoon. The corpus stored nothing about where a row
came from beyond `source`, so the answer had to be assembled from log lines: the harvest logged
`ran 66 quer(ies)` for one account, that account's config held 22 keywords across 3 locations, 22×3
is 66, and the other account's line every pass was `has no proxy credential`. From that it follows
that the rows came from the first account's searches and that LinkedIn's matching is looser than
its query — but it *follows*, it was not read.

Two columns make it readable:

- `discovered_via` — the search term that returned this link. LinkedIn's search is not exact-phrase,
  so the term that found a posting and the posting's own title can differ a great deal, and that
  gap is exactly what nobody could see. With this, "which query returned this mechanical engineering
  role" is a `SELECT`.
- `discovered_by` — the account whose harvest produced it, for the per-user tiers only. `NULL` for
  the shared cycle, where a query is folded across every account that wanted it and no single owner
  exists. Nullable and un-backfilled: every row already in the corpus predates this and pretending
  otherwise would be worse than admitting it.

`ON DELETE SET NULL` on the user: deleting an account must not delete corpus rows other accounts
are scored against, and a posting that outlives the account that found it is still a posting.

Revision ID: f2a91c47b6d8
Revises: e5c91a3b7d24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f2a91c47b6d8"
down_revision: str | None = "e5c91a3b7d24"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("corpus", sa.Column("discovered_via", sa.Text(), nullable=True))
    op.add_column("corpus", sa.Column("discovered_by", postgresql.UUID(as_uuid=True),
                                      nullable=True))
    op.create_foreign_key(op.f("fk_corpus_discovered_by_users"), "corpus", "users",
                          ["discovered_by"], ["id"], ondelete="SET NULL")
    # The question this exists to answer is always "which rows did this account's harvest find",
    # so the index is on the owner. Partial: the shared cycle leaves it NULL, which is most rows.
    op.create_index(op.f("ix_corpus_discovered_by"), "corpus", ["discovered_by"],
                    postgresql_where=sa.text("discovered_by IS NOT NULL"))


def downgrade() -> None:
    op.drop_index(op.f("ix_corpus_discovered_by"), table_name="corpus")
    op.drop_constraint(op.f("fk_corpus_discovered_by_users"), "corpus", type_="foreignkey")
    op.drop_column("corpus", "discovered_by")
    op.drop_column("corpus", "discovered_via")
