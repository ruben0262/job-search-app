"""Document text belonging to one user.

Holds the *extracted text* and never the original bytes (`libs.documents`). One row per uploaded
file, cascading from the user like everything else — which is what keeps account deletion a single
statement rather than cleanup somebody has to remember.
"""
from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from libs.db.base import Base, timestamptz

# What the user said the document is. Drives nothing structural — the processing step reads all of
# them — but it lets the form group uploads and lets a user see what is missing.
DOCUMENT_KINDS = ("cv", "cover_letter", "reference", "achievement", "diploma", "linkedin",
                  "history", "other")


class UserDocument(Base):
    __tablename__ = "user_documents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    kind: Mapped[str] = mapped_column(String(32))
    # Shown back to the user so they can tell two uploads apart. Not a path — nothing is on disk.
    filename: Mapped[str] = mapped_column(Text)

    text: Mapped[str] = mapped_column(Text)
    # Of the extracted text, not the file: two exports of the same CV in different formats should
    # be recognisable as the same content, and the file bytes would differ.
    text_sha256: Mapped[str] = mapped_column(String(64))
    chars: Mapped[int] = mapped_column(Integer)

    uploaded_at: Mapped[timestamptz] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint(f"kind IN {DOCUMENT_KINDS}", name="kind_valid"),
        # The processing step reads every document a user has, in upload order.
        Index("ix_user_documents_owner", "user_id", "uploaded_at"),
        # Re-uploading the same file replaces rather than duplicates: users press the button twice.
        Index("uq_user_documents_content", "user_id", "text_sha256", unique=True),
    )
