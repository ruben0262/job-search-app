"""Leasing, metering, and health for per-user proxy credentials."""
from __future__ import annotations

import logging
import math
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from libs import limits
from libs.db.models import ProxyCredential, User
from services.harvester.fetch import ProxyCapExceeded, ProxyLease

logger = logging.getLogger(__name__)

# Consecutive failures before a credential is quarantined.
QUARANTINE_AFTER_FAILURES = 3


class ProxyUsageRepository:
    """Per-user: `user_id` is required on every method, which is what makes attribution correct."""

    def __init__(self, session: Session):
        self._s = session

    # ── leasing ──────────────────────────────────────────────────────────────────────────────

    def acquire_lease(self, user_id: UUID, *, secret_resolver) -> ProxyLease | None:
        """Authorise this user's LinkedIn loop, or return `None` if they may not run one."""
        credential = self._s.get(ProxyCredential, user_id)
        if credential is None or credential.status != "healthy":
            return None

        self.roll_daily_window(user_id)
        remaining = float(self._cap(user_id, credential)) - float(credential.mb_used_today)
        if remaining <= 0:
            return None

        proxy_url = secret_resolver(credential.secret_ref)
        if not proxy_url:
            # A credential row whose secret cannot be resolved is broken, not merely idle.
            self.record_failure(user_id, "proxy secret could not be resolved")
            return None

        return ProxyLease(user_id=user_id, proxy_url=proxy_url, remaining_mb=remaining)

    def check_admission(self, user_id: UUID, *, projected_mb: float = 0.0) -> None:
        """Raise if this user is over budget. Called before each request, not during it."""
        credential = self._s.get(ProxyCredential, user_id)
        if credential is None:
            raise ProxyCapExceeded(f"user {user_id} has no proxy credential")
        used = float(credential.mb_used_today) + projected_mb
        cap = self._cap(user_id, credential)
        if used >= float(cap):
            raise ProxyCapExceeded(
                f"user {user_id} is at their daily proxy cap ({used:.1f}/{cap} MB)")

    def _cap(self, user_id: UUID, credential: ProxyCredential) -> float:
        """This account's daily ceiling, from the admin panel first."""
        user = self._s.get(User, user_id)
        if user is None:
            return float(credential.daily_cap_mb)
        override = limits.resolve(user, "proxy_mb_per_day")
        # `None` is Unlimited, not "fall back to the column".
        return math.inf if override is None else float(override)

    # ── metering ─────────────────────────────────────────────────────────────────────────────

    def record_usage(self, user_id: UUID, megabytes: float) -> float:
        """Add spend to the counter."""
        credential = self._s.get(ProxyCredential, user_id)
        if credential is None:
            return 0.0
        credential.mb_used_today = float(credential.mb_used_today) + max(0.0, megabytes)
        credential.last_used_at = datetime.now(_tz(self._s, user_id))
        return float(credential.mb_used_today)

    def roll_daily_window(self, user_id: UUID, *, now: datetime | None = None) -> bool:
        """Reset the counter if the user's local day has turned."""
        credential = self._s.get(ProxyCredential, user_id)
        if credential is None:
            return False

        tz = _tz(self._s, user_id)
        now = now.astimezone(tz) if now else datetime.now(tz)
        last = credential.usage_reset_at

        if last is None:
            # First time this credential is seen: initialise the window, do not zero the counter.
            credential.usage_reset_at = now
            return False

        if last.astimezone(tz).date() == now.date():
            return False

        credential.mb_used_today = 0
        credential.usage_reset_at = now
        return True

    # ── health ───────────────────────────────────────────────────────────────────────────────

    def record_success(self, user_id: UUID) -> None:
        """A working fetch clears the streak. Consecutive means consecutive."""
        credential = self._s.get(ProxyCredential, user_id)
        if credential is not None:
            credential.fail_streak = 0
            credential.last_error = None

    def record_failure(self, user_id: UUID, error: str) -> bool:
        """Record a failure; quarantine after `QUARANTINE_AFTER_FAILURES` in a row."""
        credential = self._s.get(ProxyCredential, user_id)
        if credential is None:
            return False

        credential.fail_streak += 1
        credential.last_error = error[:500]
        if credential.fail_streak >= QUARANTINE_AFTER_FAILURES and credential.status == "healthy":
            credential.status = "quarantined"
            return True
        return False

    def restore(self, user_id: UUID) -> None:
        """Return a quarantined credential to service, e.g. after the user re-enters it."""
        credential = self._s.get(ProxyCredential, user_id)
        if credential is not None:
            credential.status = "healthy"
            credential.fail_streak = 0
            credential.last_error = None


def _tz(session: Session, user_id: UUID) -> ZoneInfo:
    """The user's IANA timezone, defaulting to UTC if unset rather than raising."""
    name = session.scalar(select(User.timezone).where(User.id == user_id)) or "UTC"
    try:
        return ZoneInfo(name)
    except Exception:                                    # noqa: BLE001 - any bad tz falls back
        # Logged, not swallowed.
        logger.warning("user %s has an unusable timezone %r; daily caps will reset at UTC "
                       "midnight until it is corrected", user_id, name)
        return ZoneInfo("UTC")
