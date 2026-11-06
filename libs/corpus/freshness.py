"""When a posting can still reach somebody: one rule, read by everything that asks."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from libs import settings
from libs.settings import DEFAULT_SOURCE_MIN_AGE_HOURS, SOURCE_MIN_AGE_HOURS

# A posting time at midnight is a date, not a time, and so is none at all: a board that gives only a day.
DATE_ONLY = {"$or": [{"$eq": [{"$ifNull": ["$posted_at", None]}, None]},
                     {"$eq": ["$posted_at", {"$dateTrunc": {"date": "$posted_at", "unit": "day"}}]}]}


@dataclass(frozen=True)
class PlatformWindows:
    """The two operator settings every account's filters are built from.

    Identical for every account by definition — they are platform settings — and `filters_for` read
    both from the database **once per account**. In a loop over every account that is 2N queries
    returning the same two answers. Measured at fifty accounts: 351 queries for one health check.

    Read once with `platform_windows` and passed down. Not a cache: nothing is stored and nothing
    expires, so there is no second copy to go stale (CLAUDE.md §1).
    """

    max_age_hours: int
    source_min_ages: tuple[tuple[str, int], ...]


def platform_windows(session) -> PlatformWindows:
    """Read the platform-wide filter settings once, for a caller about to loop over accounts."""
    return PlatformWindows(
        max_age_hours=int(settings.get(session, "job_max_age_hours")),
        source_min_ages=tuple(sorted(settings.source_min_ages(session).items())))


def showable(windows: PlatformWindows, *, now: datetime | None = None) -> dict:
    """Whether a posting is still young enough to reach somebody, judged per source: the Jobs page's rule
    (`libs.tenant.filters`), and so the text queue's (`repository.awaiting_description_conditions`), the
    words search's (`libs.corpus.terms`) and the admin's (`libs.ops`). One rule, because a second one
    disagreed: the text queue read LinkedIn's date-only time as midnight, so every LinkedIn job still
    without its text left the queue at midnight UTC, one listed at 23:50 included (2026-11-05).

    **One global window does not mean "fresher jobs", it means "LinkedIn only".** Measured against
    the live corpus on 2026-08-09 with the operator's window at six hours, fifteen of seventeen
    sources returned nothing — a board that publishes a few times a day has nothing posted in the
    last six hours, so the window was switching sources off rather than raising the bar. Each
    source is therefore given at least the window it can actually serve (`min_age_hours`), and the
    operator's setting applies on top of that rather than underneath it.

    The second half is about which clock to read. Most sources report a real posting time and are
    judged on it. A few report a date only — every `posted_at` at midnight — so by mid-morning
    their whole catalogue looks half a day old however fresh it is. Those are judged on
    `first_seen_at` instead, which is trustworthy here because the harvest runs every fifteen
    minutes and every ATS token is polled every cycle: when we first saw a posting is a better
    statement about its age than a date stamped to midnight.
    """
    reference = now or datetime.now(UTC)
    floors = dict(windows.source_min_ages) or SOURCE_MIN_AGE_HOURS
    cutoff = {"$switch": {
        "branches": [{"case": {"$eq": ["$source", source]},
                      "then": reference - timedelta(hours=max(hours, windows.max_age_hours))}
                     for source, hours in sorted(floors.items())],
        "default": reference - timedelta(hours=max(DEFAULT_SOURCE_MIN_AGE_HOURS, windows.max_age_hours))}}
    # Judge a date with no time, and a posting with none, on when we found it.
    clock = {"$cond": [DATE_ONLY, "$first_seen_at", "$posted_at"]}
    # The rule above is computed per posting, so no index can narrow by it. Said plainly as well, the
    # bound it implies is what the words index reads by (`open_terms_newest`), newest first, and stops.
    # Without it, at 1.5 million postings a match read every one with a person's words, up to 586,000,
    # and took 199 s (2026-11-04).
    since = candidate_since(windows.max_age_hours, windows.source_min_ages, now=reference)
    return {"$and": [{"first_seen_at": {"$gte": since}}, {"$expr": {"$gte": [clock, cutoff]}}]}


def candidate_since(max_age_hours: int, source_min_ages, *, now: datetime) -> datetime:
    """The oldest a posting can have been found and still be anybody's candidate: the widest window any
    source has (`showable`), and a day to spare for a board whose clock runs ahead of ours. Nothing
    older is matched, so nothing older is searched for a new word (`libs.corpus.terms`)."""
    floors = dict(source_min_ages) or SOURCE_MIN_AGE_HOURS
    widest = max([*floors.values(), DEFAULT_SOURCE_MIN_AGE_HOURS, max_age_hours])
    return now - timedelta(hours=widest + 24)
