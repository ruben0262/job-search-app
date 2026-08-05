"""The tier an account is on. **A label, not a limits mechanism.**

`plans.limits` was the contract until 2026-08-05: quotas resolved `users → plan_id → plans.limits`
with a per-user JSONB override on top. That was a second per-user limits system beside
`libs.limits`, duplicating two of its keys, enforcing exactly one limit in the whole tree, and
carrying an override column nothing ever wrote — so the support grant it existed for could not be
granted. It is gone, and `libs.limits` is the one answer to "what is this account allowed to do"
(ROADMAP 6as).

What remains is the tier itself, because `plan_id` is shown on the account and admin pages and is
where a paid tier will hang when there is one. One row today: `free`.
"""
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
