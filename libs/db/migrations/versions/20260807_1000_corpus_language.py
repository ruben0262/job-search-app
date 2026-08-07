"""The language a posting is written in, as a column rather than a detection per user per pass.

The language filter has never run in production. `user_config.languages` exists in the schema and in
the onboarding type, and **no control anywhere writes it** — so the set is empty for every account,
`langfilter.is_allowed` returns True on an empty set, and a Hungarian mechanical engineer receives
German postings he cannot apply to.

Two things were wrong, and this column fixes the expensive one. Detection ran inside
`UserFilters.unwanted`, on the description, **once per candidate per account per scoring pass** — the
same posting detected again for every user and again on every retrain. It is a property of the
posting, not of the reader, so it is read once at describe time and stored beside `country`, which
is here for exactly the same reason.

NULL means the text was too short to judge (under 40 characters) or the detector could not decide.
Those pass every filter, which is the same fail-open rule the country column follows: a wrong
language hides a job somebody wanted, an unknown one costs nothing.

Backfilled here, because a column that is correct for new rows and empty for everything already
stored would hide the existing corpus from the first account that picks a language.

Revision ID: d4b8f1c37a92
Revises: c9d2e18a4f75
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "d4b8f1c37a92"
down_revision: str | None = "c9d2e18a4f75"
branch_labels: str | None = None
depends_on: str | None = None

# Read in batches, so a corpus that has grown by an order of magnitude does not build one enormous
# transaction to fill a nullable column.
BATCH = 5_000


def upgrade() -> None:
    op.add_column("corpus", sa.Column("language", sa.String(length=8), nullable=True))
    op.create_index("ix_corpus_language", "corpus", ["language"],
                    postgresql_where=sa.text("language IS NOT NULL"))

    # In Python, because the rule lives in Python. Reimplementing detection in SQL would be a second
    # definition of the one thing this column means.
    from libs.corpus.langfilter import detect_language  # noqa: PLC0415

    connection = op.get_bind()
    rows = connection.execute(sa.text(
        "SELECT id, description FROM corpus WHERE description IS NOT NULL")).fetchall()

    updates = [{"row_id": row.id, "code": code}
               for row in rows
               if (code := detect_language(row.description)) is not None]

    statement = sa.text("UPDATE corpus SET language = :code WHERE id = :row_id")
    for start in range(0, len(updates), BATCH):
        connection.execute(statement, updates[start:start + BATCH])


def downgrade() -> None:
    op.drop_index("ix_corpus_language", table_name="corpus")
    op.drop_column("corpus", "language")
