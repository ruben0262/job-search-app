"""Search terms an operator injects into the harvest.

The corpus is shared and only ever holds what somebody has already searched for, so every account
after the first inherits the shape of the ones before it. Seeding ahead of demand is what gives a
new user in a new field training data on their first day rather than in a fortnight.

**Separate from a user's own keywords, deliberately.** The two behave differently under deletion: an
operator may remove their own injected terms, and must never be able to remove somebody's real
search, which is theirs and is what their feed is built from. Keeping them in one list would make
that distinction a condition somebody has to remember rather than a property of the schema.
"""
from __future__ import annotations

from sqlalchemy import Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from libs.db.base import Base, timestamptz


class OperatorKeyword(Base):
    __tablename__ = "operator_keywords"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    keyword: Mapped[str] = mapped_column(String(100))
    # Absent means every location the platform already searches, which is what you want when
    # seeding a field rather than a place.
    location: Mapped[str | None] = mapped_column(String(64))
    # Why this term exists, for the operator who finds it in six months and wonders.
    note: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[timestamptz] = mapped_column(server_default=func.now())
    created_by: Mapped[str | None] = mapped_column(String(200))

    __table_args__ = (
        UniqueConstraint("keyword", "location", name="uq_operator_keywords_term"),
    )
