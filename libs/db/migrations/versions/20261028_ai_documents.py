"""Gemini and OpenAI keys, documents built on the site, and the platform's AI allowance

Revision ID: 24aa78d30e96
Revises: 60ea03bb2a00
Create Date: 2026-10-28 18:00:00.000000

ROADMAP Phase 8, the documents workstream. Two things:

- A person may bring a Gemini or an OpenAI key for AI-written documents, beside the two Claude kinds.
  The CHECK constraint is hand-written because autogenerate cannot see a CHECK change
  (`libs/db/README.md`).
- A CV or cover letter is downloaded from the site instead of being sent to Telegram, so its bytes
  must wait somewhere between the docgen worker and the download. `built_documents` holds them,
  keyed by the docgen queue job that built them, and the worker deletes each one once it is
  `document_keep_hours` old (D9). UNLOGGED: never in the WAL, and the nightly dump skips its rows.
- Every person gets a small daily allowance of AI requests on the platform's own paid key, across
  documents and (next) the support chat. `ai_requests` is the one count of it: a row per request
  the platform paid for, read by `libs.llm.ai_requests_today`.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "24aa78d30e96"
down_revision: str | None = "60ea03bb2a00"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The bare name: the naming convention in `libs.db.base` prefixes it.
_CONSTRAINT = "kind_valid"
_OLD = "kind IN ('claude_api', 'claude_oauth', 'telegram_bot', 'proxy')"
_NEW = ("kind IN ('claude_api', 'claude_oauth', 'telegram_bot', 'proxy', 'gemini_api', "
        "'openai_api')")


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "user_secrets", type_="check")
    op.create_check_constraint(_CONSTRAINT, "user_secrets", _NEW)

    op.create_table(
        "built_documents",
        sa.Column("build_id", sa.BigInteger(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.ForeignKeyConstraint(["build_id"], ["jobs.id"],
                                name=op.f("fk_built_documents_build_id_jobs"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("build_id", name=op.f("pk_built_documents")),
        prefixes=["UNLOGGED"],
    )

    op.create_table(
        "ai_requests",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"],
                                name=op.f("fk_ai_requests_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_requests")),
    )
    op.create_index("ix_ai_requests_user_id_created_at", "ai_requests", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_ai_requests_user_id_created_at", table_name="ai_requests")
    op.drop_table("ai_requests")
    op.drop_table("built_documents")
    # Keys of the two new kinds would violate the old constraint, so they go first.
    op.execute("DELETE FROM user_secrets WHERE kind IN ('gemini_api', 'openai_api')")
    op.drop_constraint(_CONSTRAINT, "user_secrets", type_="check")
    op.create_check_constraint(_CONSTRAINT, "user_secrets", _OLD)
