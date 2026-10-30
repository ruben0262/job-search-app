"""Leasing, metering, and health for per-user proxy credentials."""
from __future__ import annotations

import logging
import math
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from pymongo import ReturnDocument
from pymongo.database import Database

from libs import limits
from libs.db import find_one, get, now
from libs.db.models import ProxyCredential, User
from services.harvester.fetch import ProxyCapExceeded, ProxyLease

logger = logging.getLogger(__name__)

# Consecutive failures before a credential is quarantined.
QUARANTINE_AFTER_FAILURES = 3


class ProxyUsageRepository:
    """Per-user: `user_id` is required on every method, which is what makes attribution correct."""

    def __init__(self, db: Database):
        self._db = db
        self._proxies = db[ProxyCredential.COLLECTION]

    def _credential(self, user_id: UUID) -> ProxyCredential | None:
        return find_one(self._db, ProxyCredential, {"user_id": user_id})

    def _set(self, user_id: UUID, **fields) -> None:
        self._proxies.update_one({"user_id": user_id}, {"$set": {**fields, "updated_at": now()}})

    # ── leasing ──────────────────────────────────────────────────────────────────────────────

    def acquire_lease(self, user_id: UUID, *, secret_resolver) -> ProxyLease | None:
        """Authorise this user's LinkedIn loop, or return `None` if they may not run one."""
        credential = self._credential(user_id)
        if credential is None or credential.status != "healthy":
            return None

        if self.roll_daily_window(user_id):
            credential = self._credential(user_id)
        remaining = self._cap(user_id) - float(credential.mb_used_today)
        if remaining <= 0:
            return None

        proxy_url = secret_resolver(credential.secret_ref)
        if not proxy_url:
            # A credential whose secret cannot be resolved is broken, not merely idle.
            self.record_failure(user_id, "proxy secret could not be resolved")
            return None

        return ProxyLease(user_id=user_id, proxy_url=proxy_url, remaining_mb=remaining)

    def check_admission(self, user_id: UUID, *, projected_mb: float = 0.0) -> None:
        """Raise if this user is over budget. Called before each request, not during it."""
        credential = self._credential(user_id)
        if credential is None:
            raise ProxyCapExceeded(f"user {user_id} has no proxy credential")
        used = float(credential.mb_used_today) + projected_mb
        cap = self._cap(user_id)
        if used >= float(cap):
            raise ProxyCapExceeded(
                f"user {user_id} is at their daily proxy cap ({used:.1f}/{cap} MB)")

    def _cap(self, user_id: UUID) -> float:
        """This account's daily ceiling: its `proxy_mb_per_day` limit, set in the admin panel. An
        account that no longer exists has none."""
        user = get(self._db, User, user_id)
        cap = limits.resolve(user, "proxy_mb_per_day") if user else 0
        # `None` is Unlimited.
        return math.inf if cap is None else float(cap)

    # ── metering ─────────────────────────────────────────────────────────────────────────────

    def record_usage(self, user_id: UUID, megabytes: float) -> float:
        """Add spend to the counter: in the database, so two requests ending together both count."""
        spent = self._proxies.find_one_and_update(
            {"user_id": user_id},
            {"$inc": {"mb_used_today": max(0.0, megabytes)},
             "$set": {"last_used_at": datetime.now(_tz(self._db, user_id))}},
            {"mb_used_today": 1}, return_document=ReturnDocument.AFTER)
        return float(spent["mb_used_today"]) if spent else 0.0

    def roll_daily_window(self, user_id: UUID, *, now: datetime | None = None) -> bool:
        """Reset the counter if the user's local day has turned."""
        credential = self._credential(user_id)
        if credential is None:
            return False

        tz = _tz(self._db, user_id)
        now = now.astimezone(tz) if now else datetime.now(tz)
        last = credential.usage_reset_at

        if last is None:
            # First time this credential is seen: initialise the window, do not zero the counter.
            self._set(user_id, usage_reset_at=now)
            return False

        if last.astimezone(tz).date() == now.date():
            return False

        self._set(user_id, mb_used_today=0.0, usage_reset_at=now)
        return True

    # ── health ───────────────────────────────────────────────────────────────────────────────

    def record_success(self, user_id: UUID) -> None:
        """A working fetch clears the streak. Consecutive means consecutive."""
        self._set(user_id, fail_streak=0, last_error=None)

    def record_failure(self, user_id: UUID, error: str) -> bool:
        """Record a failure; quarantine after `QUARANTINE_AFTER_FAILURES` in a row."""
        credential = self._credential(user_id)
        if credential is None:
            return False
        streak = credential.fail_streak + 1
        quarantined = streak >= QUARANTINE_AFTER_FAILURES and credential.status == "healthy"
        self._set(user_id, fail_streak=streak, last_error=error[:500],
                  **({"status": "quarantined"} if quarantined else {}))
        return quarantined

    def restore(self, user_id: UUID) -> None:
        """Return a quarantined credential to service, e.g. after the user re-enters it."""
        self._set(user_id, status="healthy", fail_streak=0, last_error=None)


def _tz(db: Database, user_id: UUID) -> ZoneInfo:
    """The user's IANA timezone, defaulting to UTC if unset rather than raising."""
    name = (db[User.COLLECTION].find_one({"_id": user_id}, {"timezone": 1}) or {}).get("timezone") or "UTC"
    try:
        return ZoneInfo(name)
    except Exception:                                    # noqa: BLE001 - any bad tz falls back
        # Logged, not swallowed.
        logger.warning("user %s has an unusable timezone %r; daily caps will reset at UTC "
                       "midnight until it is corrected", user_id, name)
        return ZoneInfo("UTC")
