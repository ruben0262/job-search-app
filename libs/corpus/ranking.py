"""Which job to act on first — the port of `store.opportunity_score` and `store.priority_key`."""
from __future__ import annotations

from datetime import UTC, datetime

# Bucket edges in hours.
_FRESH_EDGES = (6, 24, 72)

# Points per bucket.
_YOUTH_POINTS = (50, 38, 19, 6)
_SCARCITY_POINTS = (50, 35, 19, 6)

# Applicant-count edges.
_APPLICANT_EDGES = (10, 50, 200)

# Apply-order preference.
_WORKPLACE_RANK = {"remote": 0, "hybrid": 1, "onsite": 3}
_WORKPLACE_UNKNOWN = 1


def fresh_bucket(*, posted_at: datetime | None, first_seen_at: datetime | None = None,
                 reposted: bool | None = None, now: datetime | None = None) -> int:
    """0 (newest) to 3 (old or unknown)."""
    if reposted:
        return len(_FRESH_EDGES)
    when = posted_at or first_seen_at
    if when is None:
        return len(_FRESH_EDGES)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    hours = ((now or datetime.now(UTC)) - when).total_seconds() / 3600
    for bucket, edge in enumerate(_FRESH_EDGES):
        if hours < edge:
            return bucket
    return len(_FRESH_EDGES)


def applicant_bucket(applicants: int | None) -> int:
    """0 (few) to 3 (many or unknown)."""
    if not isinstance(applicants, int):
        return len(_APPLICANT_EDGES)
    for bucket, edge in enumerate(_APPLICANT_EDGES):
        if applicants < edge:
            return bucket
    return len(_APPLICANT_EDGES)


def opportunity_score(*, posted_at: datetime | None = None, first_seen_at: datetime | None = None,
                      applicants: int | None = None, reposted: bool | None = None,
                      accepting: bool | None = None, now: datetime | None = None) -> int:
    """0–100, "worth applying to now"."""
    if accepting is False:
        return 0
    youth = _YOUTH_POINTS[fresh_bucket(posted_at=posted_at, first_seen_at=first_seen_at,
                                       reposted=reposted, now=now)]
    return youth + _SCARCITY_POINTS[applicant_bucket(applicants)]


def priority_key(*, posted_at: datetime | None = None, first_seen_at: datetime | None = None,
                 applicants: int | None = None, reposted: bool | None = None,
                 workplace: str | None = None, score: int | None = None,
                 now: datetime | None = None) -> tuple[int, int]:
    """Sort key for apply order — ascending, so the best job sorts first."""
    rank = (fresh_bucket(posted_at=posted_at, first_seen_at=first_seen_at,
                         reposted=reposted, now=now)
            + applicant_bucket(applicants)
            + _WORKPLACE_RANK.get(workplace or "", _WORKPLACE_UNKNOWN))
    return rank, -(score or 0)


def applicant_glut(*, source: str | None, applicants: int | None, cap: int) -> bool:
    """Whether to drop a listing as over-subscribed."""
    if cap <= 0 or not isinstance(applicants, int):
        return False
    return (source or "").lower() == "linkedin" and applicants >= cap
