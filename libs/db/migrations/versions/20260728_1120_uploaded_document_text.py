"""uploaded document text

Revision ID: b64d2f31a807
Revises: a913c5f0e742
Create Date: 2026-07-28 11:20:00.000000

Extracted text only — the original file is never written to disk (`libs/documents`). Nothing
downstream reads a PDF, and keeping strangers' diplomas and references would be a liability with no
consumer.

The unique index on `(user_id, text_sha256)` makes a re-upload replace rather than duplicate, which
matters because users press the button twice.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b64d2f31a807"
down_revision: str | None = "a913c5f0e742"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_documents",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_sha256", sa.String(length=64), nullable=False),
        sa.Column("chars", sa.Integer(), nullable=False),
        sa.Column("uploaded_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.CheckConstraint(
            "kind IN (\'cv\', \'cover_letter\', \'reference\', \'achievement\', "
            "\'diploma\', \'linkedin\', \'history\', \'other\')",
            name=op.f("ck_user_documents_kind_valid")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"],
                                name=op.f("fk_user_documents_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_documents")),
    )
    op.create_index("ix_user_documents_owner", "user_documents", ["user_id", "uploaded_at"])
    op.create_index("uq_user_documents_content", "user_documents", ["user_id", "text_sha256"],
                    unique=True)


def downgrade() -> None:
    op.drop_index("uq_user_documents_content", table_name="user_documents")
    op.drop_index("ix_user_documents_owner", table_name="user_documents")
    op.drop_table("user_documents")
