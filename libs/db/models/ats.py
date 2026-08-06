"""Shared harvester state for the direct-ATS tier: company tokens, and conditional-poll ETags."""
from __future__ import annotations

from sqlalchemy import Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from libs.db.base import Base, timestamptz


class AtsToken(Base):
    """A company's board token on one ATS."""

    __tablename__ = "ats_tokens"

    ats: Mapped[str] = mapped_column(String(32), primary_key=True)
    token: Mapped[str] = mapped_column(String(200), primary_key=True)
    first_seen_at: Mapped[timestamptz] = mapped_column(server_default=func.now())
    # Set when a board 404s.
    dead_at: Mapped[timestamptz | None]

    # The poll loop's only read: "every live token for this ATS".
    __table_args__ = (
        Index("ix_ats_tokens_live", "ats", postgresql_where=text("dead_at IS NULL")),
    )


class AtsEtag(Base):
    """The last ETag seen for one company's board."""

    __tablename__ = "ats_etags"

    ats: Mapped[str] = mapped_column(String(32), primary_key=True)
    token: Mapped[str] = mapped_column(String(200), primary_key=True)
    etag: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[timestamptz] = mapped_column(server_default=func.now(), onupdate=func.now())
