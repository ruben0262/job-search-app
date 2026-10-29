"""A CV or cover letter built for a person, waiting to be downloaded."""
from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, LargeBinary, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from libs.db.base import Base, timestamptz


class BuiltDocument(Base):
    """One finished PDF. Ephemeral (D9): the docgen worker deletes it once it is
    `document_keep_hours` old, and its build's queue row is what records that it was made.

    UNLOGGED, so it is never written to the WAL, and the nightly dump leaves its rows out
    (`--no-unlogged-table-data`): a CV kept for an hour must not live thirty days in a backup.
    """

    __tablename__ = "built_documents"

    # The docgen queue job that built it, which is the build's id, owner and state.
    build_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    filename: Mapped[str] = mapped_column(Text)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[timestamptz] = mapped_column(server_default=func.now())

    __table_args__ = {"prefixes": ["UNLOGGED"]}  # noqa: RUF012 - SQLAlchemy reads a plain attribute
