"""Jobs found by country through time, for the admin's chart (`/admin/countries`, owner 2026-11-04 and -05).

Counted as each job is stored (`CorpusRepository.add`), per run (`PLATFORM_TICK_MIN`): jobs arrive a run
at a time, so a finer count would show nothing more. Counted per hour before 2026-11-05, so a range of
hours drawn per run shows those older hours as a run each. Kept when the jobs are archived, so the chart
reaches further back than the corpus does.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pymongo.database import Database

from libs.corpus.geo import COUNTRY_LABELS
from libs.db.models import CountryCount
from libs.settings import PLATFORM_TICK_MIN

# The ranges the chart offers, in hours: an hour to a month.
RANGES_HOURS = (1, 2, 4, 8, 12, 24, 48, 168, 336, 720)


def bucket(hours: int) -> tuple[str, int]:
    """How a range is drawn: per run up to half a day, per hour up to two weeks, per day beyond. The kind,
    and its length in minutes."""
    if hours <= 12:
        return "minutes", PLATFORM_TICK_MIN
    return ("hour", 60) if hours <= 14 * 24 else ("day", 24 * 60)


def floor(moment: datetime, minutes: int) -> datetime:
    """The start of the bucket `minutes` long that `moment` falls in, counted from midnight UTC."""
    moment = moment.replace(tzinfo=moment.tzinfo or UTC)
    midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    since = int((moment - midnight).total_seconds() // 60)
    return midnight + timedelta(minutes=since - since % minutes)


def by_country(db: Database, *, hours: int, now: datetime | None = None) -> dict:
    """Each country's new jobs in the last `hours`, a count a bucket, busiest country first. The buckets
    start no earlier than the first count: before it, a zero is a count nobody took."""
    kind, minutes = bucket(hours)
    latest = floor(now or datetime.now(UTC), minutes)
    step = timedelta(minutes=minutes)
    starts = [latest - step * n for n in range(max(1, hours * 60 // minutes))][::-1]
    first = db[CountryCount.COLLECTION].find_one({}, {"hour": 1}, sort=[("hour", 1)])
    since = first["hour"].replace(tzinfo=UTC) if first else None
    if since is not None:
        starts = [start for start in starts if start >= floor(since, minutes)] or starts[-1:]
    at = {start: index for index, start in enumerate(starts)}
    found: dict[str | None, list[int]] = {}
    for row in db[CountryCount.COLLECTION].find({"hour": {"$gte": starts[0]}}):
        index = at.get(floor(row["hour"], minutes))
        if index is not None:
            found.setdefault(row["country"], [0] * len(starts))[index] += row["jobs"]
    countries = sorted(({"country": code, "name": COUNTRY_LABELS.get(code, code) if code else "Not known",
                         "total": sum(counts), "counts": counts} for code, counts in found.items()),
                       key=lambda each: -each["total"])
    return {"by": kind, "bucket_minutes": minutes, "starts": starts, "since": since, "countries": countries}
