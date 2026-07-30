"""In-app notifications: what happened to this account while nobody was looking at it.

Ported in concept from FlowSolutions' `internal/notifications` — event templates, an unread count,
mark-read — minus its websockets, mute preferences and email channel. This platform's events arrive
on service cycles measured in minutes, so a 60-second poll from the dashboard is indistinguishable
from a push and carries none of the connection machinery.

**One unread row per event, not one per occurrence.** The producers run on timers: a broken bot
token would otherwise mint a fresh "Telegram is broken" every delivery cycle, and forty copies of
the same fact is how a notification bell gets ignored. `libs.notify.notify` collapses onto the
existing unread row instead — the count stays honest ("this is still true"), the noise does not
accumulate.
"""
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

    # A dotted slug ("telegram.broken", "model.trained"). The collapse key, and what the frontend
    # may someday filter on. Free-form rather than an enum: a new producer must not need a
    # migration to speak.
    event: Mapped[str] = mapped_column(String(64))
    level: Mapped[str] = mapped_column(String(8), default="info")

    title: Mapped[str] = mapped_column(String(120))
    body: Mapped[str] = mapped_column(Text, default="")
    # An in-app hash route ("#/settings") — where clicking the notification takes you. Nullable
    # because "your model was trained" has nowhere better to point than where the reader already is.
    url: Mapped[str | None] = mapped_column(String(200))

    created_at: Mapped[timestamptz] = mapped_column(server_default=func.now())
    read_at: Mapped[timestamptz | None]

    __table_args__ = (
        # The two queries this table has: the bell's unread count, and the panel's recent list.
        Index("ix_user_notifications_unread", "user_id", "read_at"),
        Index("ix_user_notifications_recent", "user_id", "id"),
        CheckConstraint("level IN ('info', 'warning', 'error')", name="user_notifications_level"),
    )
