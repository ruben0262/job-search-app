"""How long a posting takes to reach the person it was found for."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

# How far back to measure.
WINDOW_HOURS = 24


@dataclass(frozen=True)
class LatencySpan:
    """One stage, in minutes. `None` where the window held no completed examples."""

    p50: float | None
    p90: float | None
    p99: float | None
    samples: int

    def as_dict(self) -> dict:
        return {"p50": self.p50, "p90": self.p90, "p99": self.p99, "samples": self.samples}


# The shape of both measurements.
_SPAN_SQL = """
    SELECT count(*) AS n,
           percentile_cont(0.50) WITHIN GROUP (ORDER BY minutes) AS p50,
           percentile_cont(0.90) WITHIN GROUP (ORDER BY minutes) AS p90,
           percentile_cont(0.99) WITHIN GROUP (ORDER BY minutes) AS p99
      FROM (
        SELECT EXTRACT(EPOCH FROM ({end} - {start})) / 60.0 AS minutes
          FROM user_job_state s
          JOIN corpus c ON c.id = s.job_id
         WHERE {end} IS NOT NULL AND {start} IS NOT NULL
           AND {end} >= now() - make_interval(hours => :hours)
           -- **The posting has to be recent too, not just the timestamp being measured.** Without
           -- this, a rescore of the whole corpus — which happens on every retrain — reports the
           -- age of every old row as pipeline latency: a median of 3,759 minutes, describing a
           -- backfill rather than how fast a new job reaches somebody.
           AND c.first_seen_at >= now() - make_interval(hours => :hours)
           -- A negative span is a clock change or a backfill, not a delivery. Excluded rather than
           -- averaged in, where it would quietly drag the median down.
           AND {end} >= {start}
      ) spans
"""


def _span(session: Session, start: str, end: str, *, hours: int) -> LatencySpan:
    """Percentiles of `end - start`, in minutes, over rows where both are set."""
    sql = _SPAN_SQL.format(start=start, end=end)
    row = session.execute(text(sql), {"hours": hours}).one()

    if not row.n:
        return LatencySpan(None, None, None, 0)
    return LatencySpan(round(float(row.p50), 1), round(float(row.p90), 1),
                       round(float(row.p99), 1), int(row.n))


def pipeline_latency(session: Session, *, hours: int = WINDOW_HOURS) -> dict:
    """Both spans, plus the total a user actually experiences."""
    found_to_scored = _span(session, "c.first_seen_at", "s.scored_at", hours=hours).as_dict()
    scored_to_delivered = _span(session, "s.scored_at", "s.notified_at", hours=hours).as_dict()
    # What the person waited, end to end.
    found_to_delivered = _span(session, "c.first_seen_at", "s.notified_at", hours=hours).as_dict()

    return {
        "window_hours": hours,
        "found_to_scored": _judged("found_to_scored", found_to_scored),
        "scored_to_delivered": _judged("scored_to_delivered", scored_to_delivered),
        "found_to_delivered": _judged("found_to_delivered", found_to_delivered),
        # The worst of the three, so the page has one word for "is the pipeline healthy".
        "verdict": _worst([
            _judged("found_to_scored", found_to_scored)["verdict"],
            _judged("scored_to_delivered", scored_to_delivered)["verdict"],
            _judged("found_to_delivered", found_to_delivered)["verdict"],
        ]),
    }

# Set from measured behaviour, not guessed.
THRESHOLDS: dict[str, tuple[int, int]] = {
    "found_to_scored": (30, 60),
    "scored_to_delivered": (30, 60),
    "found_to_delivered": (60, 120),
    # Per zone, for the drill-down.
    "found_to_described": (30, 60),
    # The embedder drains continuously, so minutes here mean a queue rather than a schedule.
    "described_to_embedded": (15, 45),
    "embedded_to_scored": (30, 60),
    # Ours, and tight on purpose.
    "fetched_to_stored": (1, 5),
}

# Why each threshold is where it is, in the words the expanded row shows.
THRESHOLD_REASONING: dict[str, str] = {
    "posted_to_fetched": "Not judged. The board's own publishing lag plus our tick, and sources "
                         "round what they report — so this measures their precision as much as "
                         "anything we control.",
    "fetched_to_stored": "Four dedup queries and an insert, against a gather stage that takes about "
                         "twenty seconds for a whole cycle. Seconds is normal; a minute means a "
                         "source is hanging or the ingest is contended, and both are worth a look "
                         "today.",
    "found_to_described": "The harvest runs every 15 minutes and the describe pass drains behind "
                          "it, so one missed cycle is normal and two is a backlog worth seeing.",
    "described_to_embedded": "The embedder answers in milliseconds and drains continuously. There "
                             "is no cadence to wait for here, so minutes mean a queue.",
    "embedded_to_scored": "One batch of 500 rows through an already-loaded model. Longer than a "
                          "couple of harvest cycles means the lane is not keeping up with intake.",
    "scored_to_delivered": "Delivery is a 15-minute cadence and sending is paced, so a job scored "
                           "just after a send waits most of a cycle by design. An hour means "
                           "something is not draining.",
    "found_to_delivered": "Not the sum of the rows above — it is what the user experiences, and "
                          "the only one of these worth waking up for.",
}

# Below this many samples a percentile is arithmetic rather than evidence.
MIN_SAMPLES = 20


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


# Per-lane timing, from the queue's own timestamps.
_LANE_SQL = """
    SELECT lane,
           count(*) AS n,
           percentile_cont(0.50) WITHIN GROUP (
               ORDER BY EXTRACT(EPOCH FROM (claimed_at - created_at))) AS wait_p50,
           percentile_cont(0.90) WITHIN GROUP (
               ORDER BY EXTRACT(EPOCH FROM (claimed_at - created_at))) AS wait_p90,
           percentile_cont(0.50) WITHIN GROUP (
               ORDER BY EXTRACT(EPOCH FROM (finished_at - claimed_at))) AS run_p50,
           percentile_cont(0.90) WITHIN GROUP (
               ORDER BY EXTRACT(EPOCH FROM (finished_at - claimed_at))) AS run_p90,
           max(EXTRACT(EPOCH FROM (finished_at - claimed_at))) AS run_max
      FROM jobs
     WHERE finished_at IS NOT NULL AND claimed_at IS NOT NULL
       AND finished_at >= now() - make_interval(hours => :hours)
       AND claimed_at >= created_at AND finished_at >= claimed_at
     GROUP BY lane
     ORDER BY lane
"""


def lane_timings(session: Session, *, hours: int = WINDOW_HOURS) -> list[dict]:
    """How long each lane waited and then ran, in seconds."""
    rows = session.execute(text(_LANE_SQL), {"hours": hours}).all()
    return [
        {"lane": row.lane, "samples": int(row.n),
         "wait_p50": round(float(row.wait_p50 or 0), 1),
         "wait_p90": round(float(row.wait_p90 or 0), 1),
         "run_p50": round(float(row.run_p50 or 0), 1),
         "run_p90": round(float(row.run_p90 or 0), 1),
         "run_max": round(float(row.run_max or 0), 1)}
        for row in rows]


# The end-to-end span, bucketed by hour.
_SERIES_SQL = """
    SELECT date_trunc('hour', s.notified_at) AS bucket,
           count(*) AS n,
           percentile_cont(0.50) WITHIN GROUP (ORDER BY minutes) AS p50,
           percentile_cont(0.90) WITHIN GROUP (ORDER BY minutes) AS p90
      FROM (
        SELECT s.notified_at,
               EXTRACT(EPOCH FROM (s.notified_at - c.first_seen_at)) / 60.0 AS minutes
          FROM user_job_state s
          JOIN corpus c ON c.id = s.job_id
         WHERE s.notified_at IS NOT NULL
           AND s.notified_at >= now() - make_interval(hours => :hours)
           AND c.first_seen_at >= now() - make_interval(hours => :hours)
           AND s.notified_at >= c.first_seen_at
      ) s
     GROUP BY bucket
     ORDER BY bucket
"""


def latency_series(session: Session, *, hours: int = WINDOW_HOURS) -> list[dict]:
    """Found → delivered per hour, so a spike has somewhere to show up."""
    rows = session.execute(text(_SERIES_SQL), {"hours": hours}).all()
    return [
        {"at": row.bucket.isoformat(), "samples": int(row.n),
         "p50": round(float(row.p50), 1) if row.p50 is not None else None,
         "p90": round(float(row.p90), 1) if row.p90 is not None else None}
        for row in rows]


# ── the drill-down: one row per zone ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Stage:
    """One zone of the pipeline: what happens in it, and how to time it."""

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
    # A count of what is sitting at this zone's door, where that question has an answer.
    backlog_where: str | None = None
    backlog_since: str | None = None
    # A caveat the expanded row shows above the numbers.
    caveat: str | None = None


# The pipeline, in order.
STAGES: tuple[Stage, ...] = (
    Stage(
        key="posted_to_fetched",
        label="Posted → fetched",
        zone="the board, plus our 15-minute tick",
        what="The site published the posting and our next harvest went looking. Almost none of this "
             "is ours: it is how long the board took to list the job, plus up to one cycle waiting "
             "for the next tick.",
        scope="corpus",
        start="c.posted_at",
        end="c.fetched_at",
        caveat="Sources round what they report — LinkedIn says “2 days ago” — so this number's "
               "precision is theirs, not ours. Shown because it is genuinely part of what a user "
               "waits; unjudged because a colour produced by somebody else's rounding is a colour "
               "nobody should trust.",
    ),
    Stage(
        key="fetched_to_stored",
        label="Fetched → stored",
        zone="batch wait, then dedup and insert",
        what="From the moment a source handed us this link to the moment it was a corpus row. "
             "**Most of this is waiting for the rest of the batch, not work on this row.** A "
             "cycle gathers every query concurrently and ingests only when all of them have "
             "returned, so a link parsed in the first second of a 31-second gather waits the "
             "whole 31 seconds before anything touches it. "
             "Measured 2026-08-08: `gather 31,122ms` against `ingest 1,112ms` for 125 links — the "
             "four dedup lookups and the insert are about **9ms per link**. A p50 of 84 seconds "
             "here is the batch, and speeding up dedup would move almost none of it.",
        scope="corpus",
        start="c.fetched_at",
        end="c.first_seen_at",
        caveat="`fetched_at` was added on 2026-08-04 and older rows were deliberately not "
               "backfilled — nothing recorded when their request went out, so a filled-in value "
               "would be a confident measurement of nothing. This stage is also **not** a "
               "judgement on the dedup queries: it is dominated by batch latency, and it used to "
               "be labelled as though it were not, which read as a three-minute problem where "
               "there was none.",
    ),
    Stage(
        key="found_to_described",
        label="Found → described",
        zone="describe pass",
        what="The link became text. Until a posting has a description it cannot be embedded, so "
             "nothing downstream can happen and the row is invisible to every account.",
        scope="corpus",
        start="c.first_seen_at",
        end="c.description_fetched_at",
        backlog_where="c.description_fetched_at IS NULL AND c.closed_at IS NULL",
        backlog_since="c.first_seen_at",
    ),
    Stage(
        key="described_to_embedded",
        label="Described → embedded",
        zone="embedder gateway",
        what="The text became a vector. One embed serves every account on the platform, which is "
             "why a backlog here delays everybody at once rather than one person.",
        scope="corpus",
        start="c.description_fetched_at",
        end="c.embedded_at",
        backlog_where="c.description IS NOT NULL AND c.embedding IS NULL",
        backlog_since="c.description_fetched_at",
        caveat="`embedded_at` was added on 2026-08-03 and older rows were deliberately not "
               "backfilled — filling it from another column would have invented a measurement. "
               "Until the corpus turns over, this reads from recently embedded rows only.",
    ),
    Stage(
        key="embedded_to_scored",
        label="Embedded → scored",
        zone="score lane, per account",
        what="The shared vector met this user's model. Per account, so a slow row here is one "
             "person's queue rather than the platform's.",
        scope="user",
        start="c.embedded_at",
        end="s.scored_at",
        lane="score",
    ),
    Stage(
        key="scored_to_delivered",
        label="Scored → delivered",
        zone="notify lane, 15-minute cadence",
        what="The score became a card in Telegram. A job scored just after a send waits most of a "
             "cycle by design, so this one is never near zero and is not supposed to be.",
        scope="user",
        start="s.scored_at",
        end="s.notified_at",
        lane="notify",
    ),
    Stage(
        key="found_to_delivered",
        label="Found → delivered",
        zone="the whole pipeline",
        what="What the person actually waited, from the link arriving to the card sending. Not the "
             "sum of the rows above — the percentiles of a sum are not the sum of the percentiles, "
             "and this is the only one a user would recognise.",
        scope="user",
        start="c.first_seen_at",
        end="s.notified_at",
    ),
)

_BY_KEY = {stage.key: stage for stage in STAGES}

# Where each scope's rows come from.
_SCOPE_FROM = {
    "corpus": "corpus c",
    "user": "user_job_state s JOIN corpus c ON c.id = s.job_id",
}

_STAGE_SQL = """
    SELECT count(*) AS n,
           percentile_cont(0.50) WITHIN GROUP (ORDER BY minutes) AS p50,
           percentile_cont(0.90) WITHIN GROUP (ORDER BY minutes) AS p90,
           percentile_cont(0.99) WITHIN GROUP (ORDER BY minutes) AS p99,
           max(minutes) AS worst
      FROM (
        SELECT EXTRACT(EPOCH FROM ({end} - {start})) / 60.0 AS minutes
          FROM {source}
         WHERE {end} IS NOT NULL AND {start} IS NOT NULL
           AND {end} >= now() - make_interval(hours => :hours)
           AND c.first_seen_at >= now() - make_interval(hours => :hours)
           -- A negative span is a clock change or a backfill, not a handoff.
           AND {end} >= {start}
      ) spans
"""


def _stage_span(session: Session, stage: Stage, *, hours: int) -> dict:
    """Percentiles for one zone, in minutes, plus its worst case."""
    sql = _STAGE_SQL.format(start=stage.start, end=stage.end, source=_SCOPE_FROM[stage.scope])
    row = session.execute(text(sql), {"hours": hours}).one()
    if not row.n:
        return {"p50": None, "p90": None, "p99": None, "worst": None, "samples": 0}
    return {"p50": round(float(row.p50), 1), "p90": round(float(row.p90), 1),
            "p99": round(float(row.p99), 1), "worst": round(float(row.worst), 1),
            "samples": int(row.n)}


def stage_breakdown(session: Session, *, hours: int = WINDOW_HOURS) -> list[dict]:
    """Every zone, in pipeline order, with the numbers a collapsed row shows."""
    rows = []
    for stage in STAGES:
        measured = _stage_span(session, stage, hours=hours)
        judged = stage.key in THRESHOLDS
        rows.append({
            "key": stage.key,
            "label": stage.label,
            "zone": stage.zone,
            "what": stage.what,
            "scope": stage.scope,
            "caveat": stage.caveat,
            **measured,
            # `unjudged` is not `unknown`.
            "verdict": verdict(stage.key, measured) if judged else "unjudged",
            "judged": judged,
            "amber_after_min": THRESHOLDS.get(stage.key, (None, None))[0],
            "red_after_min": THRESHOLDS.get(stage.key, (None, None))[1],
        })
    return rows


_STAGE_SERIES_SQL = """
    SELECT date_trunc('hour', ended) AS bucket,
           count(*) AS n,
           percentile_cont(0.50) WITHIN GROUP (ORDER BY minutes) AS p50,
           percentile_cont(0.90) WITHIN GROUP (ORDER BY minutes) AS p90
      FROM (
        SELECT {end} AS ended,
               EXTRACT(EPOCH FROM ({end} - {start})) / 60.0 AS minutes
          FROM {source}
         WHERE {end} IS NOT NULL AND {start} IS NOT NULL
           AND {end} >= now() - make_interval(hours => :hours)
           AND c.first_seen_at >= now() - make_interval(hours => :hours)
           AND {end} >= {start}
      ) spans
     GROUP BY bucket
     ORDER BY bucket
"""

_BACKLOG_SQL = """
    SELECT count(*) AS n,
           EXTRACT(EPOCH FROM (now() - min({since}))) / 60.0 AS oldest_min
      FROM corpus c
     WHERE {where}
"""


def _stage_series(session: Session, stage: Stage, *, hours: int) -> list[dict]:
    """One zone, bucketed by hour."""
    sql = _STAGE_SERIES_SQL.format(start=stage.start, end=stage.end,
                                   source=_SCOPE_FROM[stage.scope])
    rows = session.execute(text(sql), {"hours": hours}).all()
    return [{"at": row.bucket.isoformat(), "samples": int(row.n),
             "p50": round(float(row.p50), 1) if row.p50 is not None else None,
             "p90": round(float(row.p90), 1) if row.p90 is not None else None}
            for row in rows]


def _stage_backlog(session: Session, stage: Stage) -> dict | None:
    """How much is sitting at this zone's door, and how long the oldest has waited."""
    if not stage.backlog_where or not stage.backlog_since:
        return None
    sql = _BACKLOG_SQL.format(since=stage.backlog_since, where=stage.backlog_where)
    row = session.execute(text(sql)).one()
    return {"waiting": int(row.n),
            "oldest_min": round(float(row.oldest_min), 1) if row.oldest_min is not None else None}


# ── what the pre-score filter removed ────────────────────────────────────────────────────────────
_FILTER_SQL = """
    SELECT s.user_id,
           u.email,
           count(*) AS considered,
           count(*) FILTER (WHERE s.score IS NULL) AS removed
      FROM user_job_state s
      JOIN users u ON u.id = s.user_id
     -- **`scored_at`, not `created_at`.** The row exists from the moment anything touches the
     -- posting — a label, an applied record — and those carry a null score too. Dating the window
     -- by when the row appeared would count rows no filter ever saw as rows the filter rejected.
     -- A null `scored_at` fails this comparison, so "was it judged at all" needs no second clause.
     WHERE s.scored_at >= now() - make_interval(hours => :hours)
     GROUP BY s.user_id, u.email
     ORDER BY count(*) DESC
"""

# What the measurement cannot see, said on the page rather than left for somebody to assume.
FILTER_CAVEAT = ("Removed counts both reasons together — an excluded word and an off-centre vector "
                 "both store a null score, and the database cannot tell them apart. The split is in "
                 "the `score.timing` log line, one per batch, with the count per term.")


def filter_effect(session: Session, *, hours: int = WINDOW_HOURS) -> dict:
    """How much of each account's candidate pool the pre-score filter removed."""
    rows = session.execute(text(_FILTER_SQL), {"hours": hours}).all()

    users = []
    for row in rows:
        considered, removed = int(row.considered), int(row.removed)
        users.append({
            "user_id": str(row.user_id),
            "email": row.email,
            "considered": considered,
            "removed": removed,
            "scored": considered - removed,
            "removed_pct": round(removed * 100.0 / considered, 1) if considered else None,
            "verdict": "unknown" if considered < MIN_SAMPLES
                       else "down" if removed == considered else "ok",
        })

    considered = sum(entry["considered"] for entry in users)
    removed = sum(entry["removed"] for entry in users)
    return {
        "window_hours": hours,
        "users": users,
        "considered": considered,
        "removed": removed,
        "removed_pct": round(removed * 100.0 / considered, 1) if considered else None,
        "caveat": FILTER_CAVEAT,
        "verdict": _worst([entry["verdict"] for entry in users]),
    }


def stage_detail(session: Session, key: str, *, hours: int = WINDOW_HOURS) -> dict:
    """Everything an expanded row shows: why this zone, not just how slow."""
    stage = _BY_KEY.get(key)
    if stage is None:
        raise KeyError(key)

    lane = None
    if stage.lane:
        # One row out of the per-lane timings rather than a second query shape.
        lane = next((row for row in lane_timings(session, hours=hours)
                     if row["lane"] == stage.lane), None)

    return {
        "key": stage.key,
        "label": stage.label,
        "zone": stage.zone,
        "what": stage.what,
        "caveat": stage.caveat,
        "window_hours": hours,
        "lane_name": stage.lane,
        "lane": lane,
        "backlog": _stage_backlog(session, stage),
        "series": _stage_series(session, stage, hours=hours),
        "reasoning": THRESHOLD_REASONING.get(stage.key),
        "amber_after_min": THRESHOLDS.get(stage.key, (None, None))[0],
        "red_after_min": THRESHOLDS.get(stage.key, (None, None))[1],
    }
