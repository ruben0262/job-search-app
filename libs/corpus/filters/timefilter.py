"""Client-side recency filtering for sources without a native date cutoff.

Lifted from `job-search/pipeline/lib/timefilter.py`, unchanged apart from using `datetime.UTC`.

Two behaviours here are load-bearing and easy to "fix" into bugs:

- **An unparseable date returns True from `is_within_hours`.** Keeping a possibly-fresh posting and
  letting a later stage judge it is the safe direction; silently dropping it is not, and a source
  that changes its date format would otherwise make the feed quietly go empty.
- **`age_label` treats a midnight timestamp as date-only.** JobSpy's LinkedIn dates are midnight, so
  reporting an hour count for them would be confidently wrong; it labels by calendar day instead.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

# Non-ISO formats seen across the various job-board APIs (everything ISO-shaped —
# with/without tz, 'Z', fractional seconds, space separator — is handled by fromisoformat).
_FORMATS = ("%d-%m-%Y",)


def parse_date(date_str: str | None) -> datetime | None:
    """Best-effort parse to an aware UTC datetime. Returns None if unparseable.

    Fast path first: nearly every source emits ISO-8601 (the adapters normalise epochs and
    RSS dates to ISO), and this runs per job in every filter/sort/bucket — so one
    fromisoformat call beats a strptime format loop."""
    if not date_str:
        return None
    s = date_str.strip()
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        dt = None
        for fmt in _FORMATS:
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def epoch_to_iso(value: int | float | str | None) -> str | None:
    """Convert a Unix epoch (seconds or milliseconds) to an ISO-8601 UTC string.

    Auto-detects ms vs s by magnitude. Returns None if not a usable number.
    """
    if value is None:
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n > 1e12:          # milliseconds (e.g. 1782105022314)
        n /= 1000.0
    try:
        return datetime.fromtimestamp(n, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def is_within_hours(date_str: str | None, max_hours: int = 24) -> bool:
    """True if date_str is within the last max_hours.

    Unparseable/empty dates return True — keep the link and let Claude verify
    recency later rather than silently dropping a possibly-fresh posting.
    """
    dt = parse_date(date_str)
    if dt is None:
        return True
    cutoff = datetime.now(UTC) - timedelta(hours=max_hours)
    return dt >= cutoff


def age_label(date_str: str | None) -> str:
    """Compact 'how long ago posted' label. Handles DATE-ONLY timestamps (JobSpy's LinkedIn
    dates are midnight) — for those we don't know the hour, so we label by calendar day
    ('today' / 'Nd') instead of reporting a misleading hour count. '' if unknown."""
    dt = parse_date(date_str)
    if not dt:
        return ""
    now = datetime.now(UTC)
    if (dt.hour, dt.minute, dt.second, dt.microsecond) == (0, 0, 0, 0):
        days = (now.date() - dt.date()).days
        return "today" if days <= 0 else ("1d" if days == 1 else f"{days}d")
    hrs = (now - dt).total_seconds() / 3600
    if hrs < 1:
        return "<1h"
    if hrs < 24:
        return f"{int(hrs)}h"
    return f"{int(hrs // 24)}d"
