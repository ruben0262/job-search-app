"""Jobs found by country through time, for the admin's chart (`/admin/countries`, owner 2026-11-04, -05, -06),
counted only from the sources picked (owner, 2026-11-10).

Counted as each job is stored (`CorpusRepository.add`), per run (`PLATFORM_TICK_MIN`), country and source:
jobs arrive a run at a time, so a finer count would show nothing more. Counted per hour before 2026-11-05,
so a range of hours drawn per run shows those older hours as a run each. Kept when the jobs are archived,
so the chart reaches further back than the corpus does.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Collection
from datetime import UTC, datetime, timedelta
from math import ceil

from pymongo import DeleteOne, UpdateOne
from pymongo.database import Database

from libs.corpus.boards import board_name
from libs.corpus.geo import COUNTRY_LABELS
from libs.corpus.repository import TYPED
from libs.db.models import CorpusJob, CountryCount
from libs.settings import HARVEST_SOURCES, PLATFORM_TICK_MIN

# The ranges the chart offers, in hours: an hour to a month, and besides them everything counted.
RANGES_HOURS = (1, 2, 4, 8, 12, 24, 48, 168, 336, 720)
# What one point can stand for, in minutes: a run, half an hour, an hour, two, four, eight and twelve, a day.
# Each holds whole runs and divides a day, so every point starts at the same time of day as the last.
STEPS_MINUTES = tuple(minutes for minutes in sorted({PLATFORM_TICK_MIN, 30, 60, 120, 240, 480, 720, 24 * 60})
                      if minutes % PLATFORM_TICK_MIN == 0)
# The most points one line is drawn with, about one a pixel across a wide chart: a run a point over a month
# would be 2,880. A step that would draw more over the range is not offered.
MAX_POINTS = 1_000


def steps(hours: int) -> list[int]:
    """The steps offered over a range: those that draw it in `MAX_POINTS` or fewer, a day always."""
    fit = [minutes for minutes in STEPS_MINUTES if hours * 60 // minutes <= MAX_POINTS]
    return fit or [STEPS_MINUTES[-1]]


def bucket(hours: int) -> int:
    """The step a range is drawn with unless another is picked: a run up to half a day, an hour up to two
    weeks, a day beyond."""
    if hours <= 12:
        return PLATFORM_TICK_MIN
    return 60 if hours <= 14 * 24 else 24 * 60


def floor(moment: datetime, minutes: int) -> datetime:
    """The start of the bucket `minutes` long that `moment` falls in, counted from midnight UTC."""
    moment = moment.replace(tzinfo=moment.tzinfo or UTC)
    midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    since = int((moment - midnight).total_seconds() // 60)
    return midnight + timedelta(minutes=since - since % minutes)


def by_country(db: Database, *, hours: int | None, minutes: int | None = None,
               without: Collection[str | None] = (), now: datetime | None = None) -> dict:
    """Each country's new jobs in the last `hours`, or everything counted when it is `None`, a count a
    point `minutes` long where that step is offered (`steps`), busiest country first, from every source but
    those `without` (`None` is a count kept before sources were). Beside them each source's own, all of them,
    so one left out can be put back. The points start no earlier than the first count: before it, a zero is
    a count nobody took."""
    moment = now or datetime.now(UTC)
    first = db[CountryCount.COLLECTION].find_one({}, {"hour": 1}, sort=[("hour", 1)])
    since = first["hour"].replace(tzinfo=UTC) if first else None
    everything = hours is None
    if everything:
        hours = max(1, ceil((moment - since) / timedelta(hours=1))) if since else RANGES_HOURS[-1]
    offered = steps(hours)
    minutes = minutes if minutes in offered else bucket(hours)
    latest = floor(moment, minutes)
    step = timedelta(minutes=minutes)
    count = max(1, hours * 60 // minutes)
    if since is not None:
        # From the first count's point on: all of them for everything counted.
        counted = max(1, (latest - floor(since, minutes)) // step + 1)
        count = counted if everything else min(count, counted)
    starts = [latest - step * n for n in range(count)][::-1]
    at = {start: index for index, start in enumerate(starts)}
    # Every source read, at zero until it finds something: a board that found nothing is worth seeing.
    sources: dict[str | None, list[int]] = {name: [0] * len(starts) for name in HARVEST_SOURCES}
    found: dict[str | None, list[int]] = {}
    for row in db[CountryCount.COLLECTION].find({"hour": {"$gte": starts[0]}}):
        index = at.get(floor(row["hour"], minutes))
        if index is None:
            continue
        source = row.get("source")
        sources.setdefault(source, [0] * len(starts))[index] += row["jobs"]
        if source not in without:
            found.setdefault(row["country"], [0] * len(starts))[index] += row["jobs"]
    countries = sorted(({"country": code, "name": COUNTRY_LABELS.get(code, code) if code else "Not known",
                         "total": sum(counts), "counts": counts} for code, counts in found.items()),
                       key=lambda each: -each["total"])
    return {"bucket_minutes": minutes, "steps": offered, "starts": starts, "since": since,
            "countries": countries,
            "sources": sorted(({"source": name, "name": board_name(name) if name else "Not recorded",
                                "total": sum(counts), "counts": counts} for name, counts in sources.items()),
                              key=lambda each: (-each["total"], each["name"]))}


def split_by_source(db: Database) -> int:
    """Once: each count kept before sources were (2026-11-10) split by the sources of the jobs it counted,
    read from the corpus, so the chart's sources reach back as far as its countries do. What the corpus no
    longer holds, or holds under another country, stays under none ("Not recorded"). Every count keeps its
    total. Returns the counts split: none once done."""
    counts = db[CountryCount.COLLECTION]
    old = list(counts.find({"source": {"$exists": False}}))
    if not old:
        return 0
    found: dict[tuple, Counter] = {}
    for job in db[CorpusJob.COLLECTION].find(
            {"first_seen_at": {"$gte": min(row["hour"] for row in old)}, "source": {"$ne": TYPED}},
            {"first_seen_at": 1, "country": 1, "source": 1}):
        # Counted a run at a time, or an hour at a time before 2026-11-05.
        for minutes in (PLATFORM_TICK_MIN, 60):
            found.setdefault((floor(job["first_seen_at"], minutes), minutes, job.get("country")),
                             Counter())[job["source"]] += 1
    writes: list = []
    for row in old:
        # An hour's id has no minutes: "2026-11-04T13|DE".
        minutes = PLATFORM_TICK_MIN if ":" in row["_id"].split("|")[0] else 60
        left = row["jobs"]
        shares = found.get((floor(row["hour"], minutes), minutes, row.get("country")), Counter())
        for source, jobs in [*shares.most_common(), (None, left)]:
            take = min(jobs, left)
            left -= take
            if take:
                writes.append(UpdateOne(
                    {"_id": f"{row['hour']:%Y-%m-%dT%H:%M}|{row.get('country') or ''}|{source or ''}"},
                    {"$inc": {"jobs": take},
                     "$setOnInsert": {"hour": row["hour"], "country": row.get("country"), "source": source}},
                    upsert=True))
        writes.append(DeleteOne({"_id": row["_id"]}))
    counts.bulk_write(writes, ordered=True)
    return len(old)
