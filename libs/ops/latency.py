"""How long a posting takes to reach the person it was found for, and where that time goes."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pymongo.database import Database

from libs.corpus.freshness import platform_windows
from libs.db.models import CorpusJob, QueueJob, UserJobState
from libs.ops.steps import QUEUES, THRESHOLDS, waiting

# How far back to measure.
WINDOW_HOURS = 24

# Below this many samples a percentile is arithmetic rather than evidence.
MIN_SAMPLES = 20

# Why each limit (`steps.THRESHOLDS`) is where it is, in the words the expanded row shows.
THRESHOLD_REASONING: dict[str, str] = {
    "posted_to_fetched": "Not judged. The board's own publishing lag plus our 15-minute run, and boards "
                         "round what they report (LinkedIn gives a date, no time), so this measures their "
                         "precision as much as anything we control.",
    "fetched_to_stored": "Inside the run that listed it: LinkedIn's list is read country by country, then "
                         "every job is checked and stored. Minutes is normal; past a run's 15, the run is "
                         "hanging or the database is slow.",
    "found_to_described": "The describer reads LinkedIn's texts all the time, newest first, and each run "
                          "reads the other boards'. Inside one run's 15 minutes when nothing is behind; an "
                          "hour means it cannot keep up: proxy addresses refusing, or too few of them.",
    "found_to_delivered": "What the person waits for an alert, and the only one of these worth waking up "
                          "for: its text and words, then the notifier's next send. Three runs is normal; six "
                          "means something is not draining.",
}


@dataclass(frozen=True)
class Stage:
    """One zone of the pipeline: what happens in it, and the two timestamps that time it."""

    key: str
    label: str
    # Which service does the work. The first thing an operator needs after "this row is red".
    zone: str
    # What actually happens between the two timestamps, in a sentence.
    what: str
    scope: str  # "corpus" — once for everyone; "user" — once per account.
    start: str
    end: str
    # The queue lane that performs it, where one does.
    lane: str | None = None
    # A caveat the expanded row shows above the numbers.
    caveat: str | None = None


# The pipeline, in order.
STAGES: tuple[Stage, ...] = (
    Stage(
        key="posted_to_fetched",
        label="Posted → listed",
        zone="the board, plus our 15-minute run",
        what="The site published the posting and our next harvest went looking. Almost none of this "
             "is ours: it is how long the board took to list the job, plus up to one cycle waiting "
             "for the next tick.",
        scope="corpus", start="posted_at", end="fetched_at",
        caveat="Sources round what they report — LinkedIn says “2 days ago” — so this number's "
               "precision is theirs, not ours. Shown because it is genuinely part of what a user "
               "waits; unjudged because a colour produced by somebody else's rounding is a colour "
               "nobody should trust.",
    ),
    Stage(
        key="fetched_to_stored",
        label="Listed → stored",
        zone="the run: the whole list, then dedup and store",
        what="From the moment a board or LinkedIn's list handed us this job to the moment it was stored. "
             "**Most of this is waiting for the rest of the list, not work on this job**: every country's "
             "list is read, then every job is checked and stored.",
        scope="corpus", start="fetched_at", end="first_seen_at",
    ),
    Stage(
        key="found_to_described",
        label="Stored → text read",
        zone="the describer (LinkedIn), each run (the other boards)",
        what="The job's own text, read from its page, and everybody's words found in it in the same write. "
             "Until then the job reaches nobody.",
        scope="corpus", start="first_seen_at", end="description_fetched_at",
    ),
    Stage(
        key="found_to_delivered",
        label="Found → alert sent",
        zone="the whole way, to Telegram or a browser",
        what="What the person waited, from the job being stored to its alert being sent. The daily email "
             "is not counted.",
        scope="user", start="first_seen_at", end="notified_at", lane="notify",
    ),
)

_BY_KEY = {stage.key: stage for stage in STAGES}


def verdict(span: str, measured: dict) -> str:
    """`ok`, `warn`, `down`, or `unknown` for one span."""
    if span not in THRESHOLDS:
        return "unknown"
    samples = measured.get("samples") or 0
    p90 = measured.get("p90")
    if p90 is None or samples < MIN_SAMPLES:
        return "unknown"
    amber, red = THRESHOLDS[span]
    if p90 >= red:
        return "down"
    if p90 >= amber:
        return "warn"
    return "ok"


def _judged(span: str, measured: dict) -> dict:
    """One span's numbers with its verdict attached."""
    return {**measured, "verdict": verdict(span, measured),
            "amber_after_min": THRESHOLDS[span][0], "red_after_min": THRESHOLDS[span][1]}


def _worst(verdicts: list[str]) -> str:
    """The most serious verdict present."""
    for level in ("down", "warn", "ok"):
        if level in verdicts:
            return level
    return "unknown"


def _percentile(ordered: list[float], fraction: float) -> float:
    """Linear interpolation between the closest ranks: what Postgres's `percentile_cont` computed."""
    rank = (len(ordered) - 1) * fraction
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def _measure(minutes: list[float]) -> dict:
    """p50, p90, p99 and the worst case, in minutes."""
    if not minutes:
        return {"p50": None, "p90": None, "p99": None, "worst": None, "samples": 0}
    ordered = sorted(minutes)
    return {"p50": round(_percentile(ordered, 0.50), 1), "p90": round(_percentile(ordered, 0.90), 1),
            "p99": round(_percentile(ordered, 0.99), 1), "worst": round(ordered[-1], 1),
            "samples": len(ordered)}


def _spans(db: Database, stage: Stage, *, hours: int) -> list[tuple[datetime, float]]:
    """`(ended, minutes)` for every example of this stage inside the window.

    **The posting has to be recent too, not just the timestamp being measured**, or a backfill of old
    postings reports their age as pipeline latency. A negative span is a clock change, not a handoff,
    and is left out rather than averaged in, where it would quietly drag the median down.
    """
    since = datetime.now(UTC) - timedelta(hours=hours)
    if stage.scope == "corpus":
        rows = db[CorpusJob.COLLECTION].find(
            {stage.end: {"$gte": since}, stage.start: {"$ne": None}, "first_seen_at": {"$gte": since}},
            {"_id": 0, "start": f"${stage.start}", "end": f"${stage.end}"})
    else:
        rows = db[UserJobState.COLLECTION].aggregate([
            {"$match": {stage.end: {"$gte": since}}},
            {"$lookup": {"from": CorpusJob.COLLECTION, "localField": "job_id", "foreignField": "_id",
                         "pipeline": [{"$project": {stage.start: 1}}], "as": "job"}},
            {"$unwind": "$job"},
            {"$match": {f"job.{stage.start}": {"$gte": since}}},
            {"$project": {"_id": 0, "start": f"$job.{stage.start}", "end": f"${stage.end}"}}])
    return [(row["end"], (row["end"] - row["start"]).total_seconds() / 60)
            for row in rows if row["end"] >= row["start"]]


def _hourly(spans: list[tuple[datetime, float]]) -> list[dict]:
    """The spans bucketed by the hour they ended in, so a spike has somewhere to show up."""
    buckets: dict[datetime, list[float]] = {}
    for ended, minutes in spans:
        buckets.setdefault(ended.replace(minute=0, second=0, microsecond=0), []).append(minutes)
    return [{"at": hour.isoformat(), "samples": len(values),
             "p50": _measure(values)["p50"], "p90": _measure(values)["p90"]}
            for hour, values in sorted(buckets.items())]


def pipeline_latency(db: Database, *, hours: int = WINDOW_HOURS) -> dict:
    """What the person waited, end to end — the span the health summary judges."""
    return _delivered(_spans(db, _BY_KEY["found_to_delivered"], hours=hours), hours=hours)


def _delivered(spans: list[tuple[datetime, float]], *, hours: int) -> dict:
    found_to_delivered = _judged("found_to_delivered", _measure([minutes for _, minutes in spans]))
    return {"window_hours": hours, "found_to_delivered": found_to_delivered,
            "verdict": found_to_delivered["verdict"]}


def latency_report(db: Database, *, hours: int = WINDOW_HOURS) -> dict:
    """The drill-down whole: each zone, the end-to-end span, the lanes and the hourly series, from one
    read of each zone's spans. Found → delivered was read three times for it, once for each part
    that shows it (2026-11-03): the same numbers, three round trips to Atlas."""
    spans = {stage.key: _spans(db, stage, hours=hours) for stage in STAGES}
    return {"window_hours": hours, "stages": _breakdown(spans),
            "spans": _delivered(spans["found_to_delivered"], hours=hours),
            "lanes": lane_timings(db, hours=hours), "series": _hourly(spans["found_to_delivered"])}


def lane_timings(db: Database, *, hours: int = WINDOW_HOURS) -> list[dict]:
    """How long each lane waited and then ran, in seconds, from the queue's own timestamps."""
    since = datetime.now(UTC) - timedelta(hours=hours)
    waits: dict[str, list[float]] = {}
    runs: dict[str, list[float]] = {}
    for job in db[QueueJob.COLLECTION].find(
            {"finished_at": {"$gte": since}, "claimed_at": {"$ne": None}},
            {"lane": 1, "created_at": 1, "claimed_at": 1, "finished_at": 1}):
        if job["claimed_at"] >= job["created_at"] and job["finished_at"] >= job["claimed_at"]:
            waits.setdefault(job["lane"], []).append((job["claimed_at"] - job["created_at"]).total_seconds())
            runs.setdefault(job["lane"], []).append((job["finished_at"] - job["claimed_at"]).total_seconds())
    return [{"lane": lane, "samples": len(waits[lane]),
             "wait_p50": round(_percentile(sorted(waits[lane]), 0.50), 1),
             "wait_p90": round(_percentile(sorted(waits[lane]), 0.90), 1),
             "run_p50": round(_percentile(sorted(runs[lane]), 0.50), 1),
             "run_p90": round(_percentile(sorted(runs[lane]), 0.90), 1),
             "run_max": round(max(runs[lane]), 1)}
            for lane in sorted(waits)]


def stage_breakdown(db: Database, *, hours: int = WINDOW_HOURS) -> list[dict]:
    """Every zone, in pipeline order, with the numbers a collapsed row shows."""
    return _breakdown({stage.key: _spans(db, stage, hours=hours) for stage in STAGES})


def _breakdown(spans: dict[str, list[tuple[datetime, float]]]) -> list[dict]:
    rows = []
    for stage in STAGES:
        measured = _measure([minutes for _, minutes in spans[stage.key]])
        judged = stage.key in THRESHOLDS
        rows.append({
            "key": stage.key, "label": stage.label, "zone": stage.zone, "what": stage.what,
            "scope": stage.scope, "caveat": stage.caveat, **measured,
            # `unjudged` is not `unknown`.
            "verdict": verdict(stage.key, measured) if judged else "unjudged",
            "judged": judged,
            "amber_after_min": THRESHOLDS.get(stage.key, (None, None))[0],
            "red_after_min": THRESHOLDS.get(stage.key, (None, None))[1],
        })
    return rows


def _backlog(db: Database, stage: Stage) -> dict | None:
    """How much is waiting at the step, and how long the oldest has waited, by the step's own question
    (`steps.waiting`, which the Pipeline tab reads too). Its own here counted every job ever stored without
    text, too old to be read or not, so it grew for ever beside the Pipeline tab's count (2026-11-05)."""
    return waiting(db, stage.key, platform_windows(db)) if stage.key in QUEUES else None


def stage_detail(db: Database, key: str, *, hours: int = WINDOW_HOURS) -> dict:
    """Everything an expanded row shows: why this zone, not just how slow."""
    stage = _BY_KEY.get(key)
    if stage is None:
        raise KeyError(key)
    lane = next((row for row in lane_timings(db, hours=hours) if row["lane"] == stage.lane),
                None) if stage.lane else None
    return {
        "key": stage.key, "label": stage.label, "zone": stage.zone, "what": stage.what,
        "caveat": stage.caveat, "window_hours": hours, "lane_name": stage.lane, "lane": lane,
        "backlog": _backlog(db, stage),
        "series": _hourly(_spans(db, stage, hours=hours)),
        "reasoning": THRESHOLD_REASONING.get(stage.key),
        "amber_after_min": THRESHOLDS.get(stage.key, (None, None))[0],
        "red_after_min": THRESHOLDS.get(stage.key, (None, None))[1],
    }
