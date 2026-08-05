"""The centroid of what a user has accepted, stored beside the model that was fitted on it.

The pre-score filter of ROADMAP 6an. Both columns belong on `models` rather than on `users` because
they are derived from exactly the labels the artifact beside them was trained on: a retrain produces
a new model and a new centroid together, and an old model keeps the centroid it was measured with.
Putting them on the user would let the two drift apart with nothing saying so.

Nullable, and that is the fail-open the whole design rests on. `NULL` means "no centroid" — an
account under 25 positive labels, or a model fitted before this existed — and a caller holding no
centroid scores every row rather than none. Getting that backwards empties a feed.

Revision ID: a1f4c8d29e63
Revises: f3a7d92c6b14
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "a1f4c8d29e63"
down_revision: str | None = "f3a7d92c6b14"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # LargeBinary, in the same packed float32 format as `corpus.embedding` — one format, one reader
    # (`libs.scoring.centroid.pack`), so the filter compares vectors written the same way.
    op.add_column("models", sa.Column("centroid", sa.LargeBinary(), nullable=True))
    # The similarity below which a posting is not worth scoring for this user. Measured per account
    # from their own labels, never a platform constant: the same number cut 51% of one live
    # account's pool and 20% of another's.
    op.add_column("models", sa.Column("centroid_threshold", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("models", "centroid_threshold")
    op.drop_column("models", "centroid")
