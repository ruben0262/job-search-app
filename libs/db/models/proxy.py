"""Per-user proxy credentials — the LinkedIn tier's payment and permission in one row.

**Decision D3 (revised): every user brings their own proxy, used strictly for their own fetches,
never pooled.** An earlier design pooled credentials and was rejected: pooling created a free-rider
problem and put the platform in breach of typical residential-proxy terms, which prohibit sharing or
reselling access. Both disappear under strict per-user use, and this table is where that becomes
structural rather than a policy someone has to remember.

`user_id` is the **primary key**, not just a foreign key. One credential per user, so
"which credential paid for this fetch" has exactly one answer and attribution is correct by
construction rather than by bookkeeping (`HARVESTER_PLAN.md` §5.4).

The secret itself is **not** here — `secret_ref` points at `user_secrets`, where envelope encryption
lives (`TENANT_SAFETY.md` §2). Keeping the plaintext out of a table that also carries counters and
status means the operational columns can be read, logged, and dashboarded without ever touching the
credential.
"""
from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from libs.db.base import Base, timestamptz

# healthy    — usable; the user sees LinkedIn-sourced jobs
# quarantined— N consecutive failures; skipped, owner notified, retried later
# disabled   — the user turned it off, or it is permanently broken
PROXY_STATUSES = ("healthy", "quarantined", "disabled")


class ProxyCredential(Base):
    __tablename__ = "proxy_credentials"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)

    # Points into user_secrets; the ciphertext never lands in this table.
    secret_ref: Mapped[str] = mapped_column(Text)

    # The planning estimate, explicitly NOT a measurement. HARVESTER_PLAN.md §5.4 and
    # PLATFORM_ARCHITECTURE.md §10.7 both say to size this from a real per-user monthly GB figure
    # before it is trusted — it is the first cost a user is asked to accept, and guessing it wrong
    # is either a surprise bill for them or a tier that silently never runs.
    daily_cap_mb: Mapped[int] = mapped_column(Integer, default=200, server_default="200")
    # Numeric, not float: this is a spend counter compared against a cap, and float drift on a
    # running total is exactly the kind of error nobody notices until an invoice.
    mb_used_today: Mapped[float] = mapped_column(
        Numeric(12, 3), default=0, server_default="0")
    # Counters reset at the USER's local midnight, not UTC (G7) — users are spread across
    # timezones, so a UTC day is the wrong day for most of them. Recording when the window was last
    # rolled makes that computable without a per-user cron.
    usage_reset_at: Mapped[timestamptz | None]

    status: Mapped[str] = mapped_column(String(16), default="healthy", server_default="healthy")
    # N consecutive failures → quarantined. Consecutive, not cumulative: an occasional 407 on a
    # working credential is normal, a run of them is a broken one.
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
