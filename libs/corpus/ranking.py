"""Which job to act on first — the port of `store.opportunity_score` and `store.priority_key`.

**Not the same question as "is this a good job".** The score answers fit; this answers *worth
applying to right now*, and the difference is the whole reason the reference ranks on it rather
than on score. A perfect-fit posting three weeks old with two hundred applicants is behind a good
one posted an hour ago, because the second is winnable and the first is very likely already filled.

Two signals, equally weighted at fifty points each:

- **youth** — how long ago it was posted;
- **scarcity** — how few people have applied.

Deliberately *not* factors:

- **Easy apply.** It still shows on the card as a tag, but LinkedIn one-click no longer earns any
  priority — the reference removed it from ranking after the applications it produced went nowhere.
- **Fit score.** It gates entry (a job below the threshold is not a candidate at all) and breaks
  ties, but it does not dominate ordering: someone adaptable is served better by being first to a
  decent job than by being hundredth to an ideal one.

**A repost is treated as the oldest thing there is.** LinkedIn resurfaces stale listings with a
fresh timestamp, so "Reposted 1 hour ago" would otherwise outrank a genuinely new posting.

**A closed listing scores zero**, whatever else is true about it: you cannot apply.

The applicant count is the free guest-endpoint number and can lag the live page. Age is reliable.
That asymmetry is why both are bucketed rather than used as raw values — a bucket survives being
somewhat wrong, and an arithmetic comparison of 68 against 71 applicants does not mean anything.
"""
from __future__ import annotations

from datetime import UTC, datetime

# Bucket edges in hours. Under six is "nobody has seen this yet"; over seventy-two is a posting the
# employer has already been reading applications from for days.
_FRESH_EDGES = (6, 24, 72)

# Points per bucket. The steep drop between the first two is intentional: the difference between
# applying in the first six hours and the first day is the single biggest thing the user controls.
_YOUTH_POINTS = (50, 38, 19, 6)
_SCARCITY_POINTS = (50, 35, 19, 6)

# Applicant-count edges. LinkedIn caps its display at "over 200", so the top bucket is really "at
# least two hundred, and possibly very many more".
_APPLICANT_EDGES = (10, 50, 200)

# Apply-order preference. Unknown ranks with hybrid rather than last, because unknown is the common
# case and treating it as on-site would bury most of the feed.
_WORKPLACE_RANK = {"remote": 0, "hybrid": 1, "onsite": 3}
_WORKPLACE_UNKNOWN = 1


def fresh_bucket(*, posted_at: datetime | None, first_seen_at: datetime | None = None,
                 reposted: bool | None = None, now: datetime | None = None) -> int:
    """0 (newest) to 3 (old or unknown).

    Falls back to `first_seen_at` when the source gives no post date, which LinkedIn frequently
    does not — when we found it is a worse answer than when it was posted, but it is a real one.
    """
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
    """0 (few) to 3 (many or unknown). Unknown lands in the worst bucket on purpose.

    That is the conservative choice for *ranking*, where the cost of being wrong is ordering, not
    deletion — unlike the filters, which fail open because being wrong there loses a job entirely.
    """
    if not isinstance(applicants, int):
        return len(_APPLICANT_EDGES)
    for bucket, edge in enumerate(_APPLICANT_EDGES):
        if applicants < edge:
            return bucket
    return len(_APPLICANT_EDGES)


def opportunity_score(*, posted_at: datetime | None = None, first_seen_at: datetime | None = None,
                      applicants: int | None = None, reposted: bool | None = None,
                      accepting: bool | None = None, now: datetime | None = None) -> int:
    """0–100, "worth applying to now". Free to recompute, so it is never stored.

    Storing it would be a bug rather than an optimisation: it decays with the clock, and a value
    written at harvest would still claim the job is an hour old a week later.
    """
    if accepting is False:
        return 0
    youth = _YOUTH_POINTS[fresh_bucket(posted_at=posted_at, first_seen_at=first_seen_at,
                                       reposted=reposted, now=now)]
    return youth + _SCARCITY_POINTS[applicant_bucket(applicants)]


def priority_key(*, posted_at: datetime | None = None, first_seen_at: datetime | None = None,
                 applicants: int | None = None, reposted: bool | None = None,
                 workplace: str | None = None, score: int | None = None,
                 now: datetime | None = None) -> tuple[int, int]:
    """Sort key for apply order — ascending, so the best job sorts first.

    Adds workplace to the two opportunity signals, and keeps fit as a tiebreaker only. The reference
    is explicit that this is a judgement about *the candidate*: fit gates entry, and among jobs that
    already clear the bar, being early matters more than being ideal.
    """
    rank = (fresh_bucket(posted_at=posted_at, first_seen_at=first_seen_at,
                         reposted=reposted, now=now)
            + applicant_bucket(applicants)
            + _WORKPLACE_RANK.get(workplace or "", _WORKPLACE_UNKNOWN))
    return rank, -(score or 0)


def applicant_glut(*, source: str | None, applicants: int | None, cap: int) -> bool:
    """Whether to drop a listing as over-subscribed. LinkedIn only.

    LinkedIn is the only source reporting a count worth trusting, and its 200 display cap is the
    signal: a listing pinned at "over 200 applicants" is almost always a stale or reposted one that
    has been up for weeks. `cap <= 0` disables it, which is how a user who would rather see
    everything turns it off.
    """
    if cap <= 0 or not isinstance(applicants, int):
        return False
    return (source or "").lower() == "linkedin" and applicants >= cap
