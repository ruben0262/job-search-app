"""Client-side recency filtering for sources without a native date cutoff."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

# Non-ISO formats seen across job-board APIs; ISO-shaped strings go through `fromisoformat`.
_FORMATS = ("%d-%m-%Y",)


def parse_date(date_str: str | None) -> datetime | None:
    """Best-effort parse to an aware UTC datetime."""
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
    """Convert a Unix epoch (seconds or milliseconds) to an ISO-8601 UTC string."""
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
    """True if date_str is within the last max_hours."""
    dt = parse_date(date_str)
    if dt is None:
        return True
    cutoff = datetime.now(UTC) - timedelta(hours=max_hours)
    return dt >= cutoff


def age_label(date_str: str | None) -> str:
    """Compact 'how long ago posted' label."""
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
