"""In-app notifications: what happened to this account while nobody was looking at it."""
from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from libs.db.base import Base, timestamptz


class UserNotification(Base):
    """One thing this user should know about."""

    __tablename__ = "user_notifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    # A dotted slug ("telegram.broken", "model.trained").
    event: Mapped[str] = mapped_column(String(64))
    level: Mapped[str] = mapped_column(String(8), default="info")

    title: Mapped[str] = mapped_column(String(120))
    body: Mapped[str] = mapped_column(Text, default="")
    # An in-app hash route ("#/settings") — where clicking the notification takes you.
    url: Mapped[str | None] = mapped_column(String(200))

    created_at: Mapped[timestamptz] = mapped_column(server_default=func.now())
    read_at: Mapped[timestamptz | None]

    __table_args__ = (
        # The two queries this table has: the bell's unread count, and the panel's recent list.
        Index("ix_user_notifications_unread", "user_id", "read_at"),
        Index("ix_user_notifications_recent", "user_id", "id"),
        CheckConstraint("level IN ('info', 'warning', 'error')", name="user_notifications_level"),
    )
