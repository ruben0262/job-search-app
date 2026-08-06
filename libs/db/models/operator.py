"""Search terms an operator injects into the harvest."""
from __future__ import annotations

from sqlalchemy import Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from libs.db.base import Base, timestamptz


class OperatorKeyword(Base):
    __tablename__ = "operator_keywords"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    keyword: Mapped[str] = mapped_column(String(100))
    # Absent means every location the platform already searches.
    location: Mapped[str | None] = mapped_column(String(64))
    # Why this term exists, for the operator who finds it in six months and wonders.
    note: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[timestamptz] = mapped_column(server_default=func.now())
    created_by: Mapped[str | None] = mapped_column(String(200))

    __table_args__ = (
        UniqueConstraint("keyword", "location", name="uq_operator_keywords_term"),
    )
