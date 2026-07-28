"""Resolving one user's limits, and refusing loudly when they are reached.

Two rules from `TENANT_SAFETY.md` §3, both of which are about *where* rather than *what*:

- **Enforce at the admission point** — the enqueue call or the API handler — never deep inside a
  worker. A quota checked halfway through a job has already spent the resource it was protecting.
- **Refuse loudly.** Over-quota returns an explicit error the UI shows, naming when it resets.
  Silent truncation is how a user concludes the product is broken rather than that they hit a limit.
"""
from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from libs.db.models import Plan, User
from libs.db.models.plan import FREE_PLAN

# The `free` row's contents, and the only place these numbers appear. Seeded by migration; this
# dictionary exists so a fresh database and the migration cannot disagree, and is asserted equal to
# the stored row by a test.
FREE_LIMITS: dict[str, float] = {
    "search_terms": 25,
    "notifications_per_day": 100,
    "cv_builds_per_day": 10,
    "labels_per_day": 500,
    "claude_spend_per_day_usd": 2.0,
    "proxy_mb_per_day": 200,
    "user_fetch_mb_per_day": 50,
    "corpus_queries_per_min": 60,
}


class QuotaExceeded(RuntimeError):
    """Over limit. Carries what the UI needs to say something true and specific."""

    def __init__(self, quota: str, limit: float, used: float, resets: str = "midnight your time"):
        super().__init__(f"{quota} limit of {limit:g} reached ({used:g} used) — resets at {resets}")
        self.quota, self.limit, self.used, self.resets = quota, limit, used, resets


@dataclass(frozen=True)
class Quotas:
    """One user's effective limits: their plan, with any support override applied on top."""

    plan_id: str
    limits: dict[str, float]

    def limit(self, name: str) -> float:
        if name not in self.limits:
            # An unknown quota name is a typo, and defaulting it to "unlimited" would silently
            # remove a limit somebody thought they were enforcing.
            raise KeyError(f"no quota named {name!r}; known: {sorted(self.limits)}")
        return self.limits[name]

    def check(self, name: str, used: float, *, adding: float = 1) -> None:
        """Raise if `used + adding` would exceed the limit. Call at the admission point."""
        allowed = self.limit(name)
        if used + adding > allowed:
            raise QuotaExceeded(name, allowed, used)

    def remaining(self, name: str, used: float) -> float:
        return max(self.limit(name) - used, 0)


def quotas_for(session: Session, user_id: _uuid.UUID) -> Quotas:
    """`users → plan_id → plans.limits`, with `users.quota_overrides` merged over the top.

    The override is per key rather than wholesale: granting one extra CV build should not silently
    reset every other limit to whatever the grant happened to include.
    """
    row = session.execute(
        select(User.plan_id, User.quota_overrides, Plan.limits)
        .join(Plan, Plan.id == User.plan_id)
        .where(User.id == user_id)).first()
    if row is None:
        raise ValueError(f"no such user: {user_id}")

    plan_id, overrides, limits = row
    effective = dict(limits or {})
    effective.update(overrides or {})
    return Quotas(plan_id=plan_id or FREE_PLAN, limits=effective)
