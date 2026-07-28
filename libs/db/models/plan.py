"""Plans, and the quotas resolved from them.

**One plan at launch: `free`.** The forward-compat move is not to build tiers — it is to keep the
limits in a row rather than in code constants (`TENANT_SAFETY.md` §3). Adding a paid tier later then
becomes an INSERT, not a migration plus an audit of every hardcoded number.

Quotas resolve `users → plan_id → plans.limits`, with a per-user JSONB override for support grants:
"give this person 50 CV builds today" should not require a deploy, and should not silently become
everyone's limit.
"""
from __future__ import annotations

from sqlalchemy import String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from libs.db.base import Base, timestamptz

FREE_PLAN = "free"


class Plan(Base):
    """One pricing tier. `limits` is the whole contract; nothing about it is hardcoded elsewhere."""

    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    limits: Mapped[dict] = mapped_column(JSONB)
    updated_at: Mapped[timestamptz] = mapped_column(server_default=func.now(), onupdate=func.now())
