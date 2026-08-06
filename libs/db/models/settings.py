"""Settings the operator owns."""
from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from libs.db.base import Base, timestamptz


class PlatformSetting(Base):
    __tablename__ = "platform_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict | list | int | str] = mapped_column(JSONB)
    updated_at: Mapped[timestamptz] = mapped_column(server_default=func.now(), onupdate=func.now())
    # Who changed it. An operator setting that nobody can attribute is one nobody will own.
    updated_by: Mapped[str | None] = mapped_column(String(320))
