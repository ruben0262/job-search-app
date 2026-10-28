"""Notifications: what happened to this account while nobody was looking, and where else to say so."""
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


class PushSubscription(Base):
    """One browser that asked for job alerts (Web Push). Deleted when its push service says it is gone."""

    __tablename__ = "push_subscriptions"

    # The push service's address for this browser, which is what identifies it.
    endpoint: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"),
                                               index=True)
    # The browser's keys: what is sent is encrypted to them, so only that browser can read it.
    p256dh: Mapped[str] = mapped_column(String(256))
    auth: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[timestamptz] = mapped_column(server_default=func.now())
