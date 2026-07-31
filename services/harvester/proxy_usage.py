"""Leasing, metering, and health for per-user proxy credentials.

The database side of `fetch.py`. `fetch.py` decides *how* a request is routed; this decides
**whether a user may fetch at all**, and records what it cost.

Three rules, each from a specific failure the design anticipates:

1. **Admission, not interruption.** The cap is checked before the loop starts and again before each
   request. A quota checked halfway through a job has already spent the resource it was protecting
   (`TENANT_SAFETY.md` §3).
2. **Usage is recorded in the same transaction as the fetch it paid for.** Crediting bytes
   afterwards means a crash between the two silently gives the user free bandwidth, and the counter
   drifts under exactly the conditions — errors, retries — that make it matter
   (`HARVESTER_PLAN.md` §5.4).
3. **Counters reset at the user's local midnight, never UTC.** Users are spread across timezones,
   so a UTC day is the wrong day for most of them, and "resets at midnight your time" has to keep
   meaning the same thing in November (G7).

Health is a **consecutive** failure count, not a cumulative one. An occasional 407 on a working
credential is normal; a run of them is a broken credential. Cumulative counting would eventually
quarantine every healthy user.
"""
from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from libs.db.models import ProxyCredential, User
from services.harvester.fetch import ProxyCapExceeded, ProxyLease

logger = logging.getLogger(__name__)

# Consecutive failures before a credential is quarantined. Low enough that a genuinely dead
# credential stops burning the loop's time quickly; high enough that one flaky response does not
# take a working user off LinkedIn.
QUARANTINE_AFTER_FAILURES = 3


class ProxyUsageRepository:
    """Per-user, and `user_id` is required on every method — there is only ever one candidate
    credential for a fetch, which is what makes attribution correct by construction."""

    def __init__(self, session: Session):
        self._s = session

    # ── leasing ──────────────────────────────────────────────────────────────────────────────

    def acquire_lease(self, user_id: UUID, *, secret_resolver) -> ProxyLease | None:
        """Authorise this user's LinkedIn loop, or return `None` if they may not run one.

        `None` is the ordinary case, not an error: a user with no credential simply does not get the
        LinkedIn tier, and still receives the ~2/3 of sources that cost nothing.

        `secret_resolver` is injected rather than imported so the plaintext lives in the caller's
        scope for the shortest possible time — decryption belongs in the worker making the call,
        never anywhere it could be logged or persisted (`TENANT_SAFETY.md` §2).
        """
        credential = self._s.get(ProxyCredential, user_id)
        if credential is None or credential.status != "healthy":
            return None

        self.roll_daily_window(user_id)
        remaining = float(credential.daily_cap_mb) - float(credential.mb_used_today)
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
        if used >= float(credential.daily_cap_mb):
            raise ProxyCapExceeded(
                f"user {user_id} is at their daily proxy cap "
                f"({used:.1f}/{credential.daily_cap_mb} MB)")

    # ── metering ─────────────────────────────────────────────────────────────────────────────

    def record_usage(self, user_id: UUID, megabytes: float) -> float:
        """Add spend to the counter. Returns the new total.

        **Call this in the same transaction as the work it paid for.** Crediting afterwards means a
        crash between the two hands the user free bandwidth, and the drift accumulates exactly when
        errors and retries make the counter most worth trusting.
        """
        credential = self._s.get(ProxyCredential, user_id)
        if credential is None:
            return 0.0
        credential.mb_used_today = float(credential.mb_used_today) + max(0.0, megabytes)
        credential.last_used_at = datetime.now(_tz(self._s, user_id))
        return float(credential.mb_used_today)

    def roll_daily_window(self, user_id: UUID, *, now: datetime | None = None) -> bool:
        """Reset the counter if the user's local day has turned. Returns True if it rolled.

        Local, not UTC. A user in Budapest and one in Lisbon do not share a midnight, and a shared
        reset would give one of them a short day and the other a long one every single day.
        """
        credential = self._s.get(ProxyCredential, user_id)
        if credential is None:
            return False

        tz = _tz(self._s, user_id)
        now = now.astimezone(tz) if now else datetime.now(tz)
        last = credential.usage_reset_at

        if last is None:
            # First time this credential is seen: **initialise the window, do not zero the
            # counter.** Treating a null timestamp as "the day has turned" would discard real usage
            # the first time a credential is leased — a user who had already spent bandwidth would
            # silently get their cap back. Initialising is not a reset, so it reports False.
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
        """Record a failure; quarantine after `QUARANTINE_AFTER_FAILURES` in a row.

        Returns True if this failure caused a quarantine, so the caller can notify the owner —
        `OPERATIONS_PLAN.md` §4b: the user must be told, by email and an in-app banner, never
        through the Telegram feed. A credential that quietly stops working and tells nobody is the
        failure mode where a user concludes the product is broken.
        """
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
    """The user's IANA timezone, defaulting to UTC if unset rather than raising.

    A missing timezone must not stop a harvest — it makes the reset happen at the wrong hour for one
    user, which is a nuisance; raising would take the whole loop down, which is an outage.
    """
    name = session.scalar(select(User.timezone).where(User.id == user_id)) or "UTC"
    try:
        return ZoneInfo(name)
    except Exception:                                    # noqa: BLE001 - any bad tz falls back
        # Logged, not swallowed. The fallback is right - a broken timezone must not take the
        # harvest down - but silently resetting one user's caps at the wrong hour forever is
        # exactly the kind of failure nobody ever notices.
        logger.warning("user %s has an unusable timezone %r; daily caps will reset at UTC "
                       "midnight until it is corrected", user_id, name)
        return ZoneInfo("UTC")
