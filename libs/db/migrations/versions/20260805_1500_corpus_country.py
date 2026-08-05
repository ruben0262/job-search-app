"""The country a posting is in, as a column rather than a regex over free text.

**`libs/corpus/geo.py` had no production consumer at all.** The module, its EU vocabulary, its
reader and its forty tests were imported by tests and by nothing else — so the country filter the
owner asked for on 2026-08-04, and complained about again when American jobs kept arriving, has
never run. A value with no consumer, and the largest one this project has shipped (ROADMAP 6ak).

This is the column that makes it runnable: read once at ingest, compared as an indexed equality in
the pre-score filter instead of a regex over a location string per user per job.

NULL means the posting names no place — 16% of the corpus, mostly LinkedIn rows with an empty
location and remote-first boards that say "Worldwide". Those pass every filter, which is rule 2 of
the geo module: a wrong country hides a job somebody wanted, an unknown one costs nothing.

Backfilled in this migration, because the alternative is a column that is correct for new rows and
silently empty for the 22,856 already stored — and a filter reading it would then hide the entire
existing corpus from anyone who set a country.

Revision ID: f3a7d92c6b14
Revises: e7c2b4a91d38
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "f3a7d92c6b14"
down_revision: str | None = "e7c2b4a91d38"
branch_labels: str | None = None
depends_on: str | None = None

# Read in batches rather than one statement, so a corpus that has grown by an order of magnitude
# does not build one enormous transaction to fill a nullable column.
BATCH = 5_000


def upgrade() -> None:
    op.add_column("corpus", sa.Column("country", sa.String(length=2), nullable=True))
    op.create_index("ix_corpus_country", "corpus", ["country"],
                    postgresql_where=sa.text("country IS NOT NULL"))

    # **The backfill runs here, in Python, because the rule lives in Python.** Reimplementing
    # `read_country` as SQL would be a second definition of the one thing this column means, which
    # is the defect that produced six of the last seven incidents (ROADMAP 6ah).
    from libs.corpus.geo import read_country                       # noqa: PLC0415

    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT id, location, title FROM corpus WHERE location IS NOT NULL")).fetchall()

    updates = [{"row_id": row.id, "code": code}
               for row in rows
               if (code := read_country(row.location, title=row.title)) is not None]

    statement = sa.text("UPDATE corpus SET country = :code WHERE id = :row_id")
    for start in range(0, len(updates), BATCH):
        connection.execute(statement, updates[start:start + BATCH])


def downgrade() -> None:
    op.drop_index("ix_corpus_country", table_name="corpus")
    op.drop_column("corpus", "country")
