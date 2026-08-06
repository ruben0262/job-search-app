"""Per-user proxy credentials — the LinkedIn tier's payment and permission in one row."""
from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from libs.db.base import Base, timestamptz

# healthy: usable. quarantined: N failures, skipped and retried later. disabled: off or broken.
PROXY_STATUSES = ("healthy", "quarantined", "disabled")


class ProxyCredential(Base):
    __tablename__ = "proxy_credentials"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)

    # Points into user_secrets; the ciphertext never lands in this table.
    secret_ref: Mapped[str] = mapped_column(Text)

    # The planning estimate, explicitly NOT a measurement.
    daily_cap_mb: Mapped[int] = mapped_column(Integer, default=200, server_default="200")
    # Numeric, not float: a spend counter compared against a cap must not drift.
    mb_used_today: Mapped[float] = mapped_column(
        Numeric(12, 3), default=0, server_default="0")
    # Counters reset at the user's local midnight, not UTC (G7).
    usage_reset_at: Mapped[timestamptz | None]

    status: Mapped[str] = mapped_column(String(16), default="healthy", server_default="healthy")
    # N consecutive failures → quarantined.
    fail_streak: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    last_used_at: Mapped[timestamptz | None]

    created_at: Mapped[timestamptz] = mapped_column(server_default=func.now())
    updated_at: Mapped[timestamptz] = mapped_column(server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint(f"status IN {PROXY_STATUSES}", name="status_valid"),
        CheckConstraint("daily_cap_mb > 0", name="cap_positive"),
        CheckConstraint("mb_used_today >= 0", name="usage_non_negative"),
        CheckConstraint("fail_streak >= 0", name="fail_streak_non_negative"),
    )
