"""The tier an account is on."""
from __future__ import annotations

from sqlalchemy import String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from libs.db.base import Base, timestamptz

FREE_PLAN = "free"


class Plan(Base):
    """One pricing tier, by name. Limits live in `libs.limits`, per account."""

    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[timestamptz] = mapped_column(server_default=func.now(), onupdate=func.now())
