"""How long a posting takes to reach the person it was found for.

**The product's whole claim is timeliness, and nobody was measuring it.** Freshness windows are set
in hours; if the pipeline itself eats two of them, a four-hour window delivers jobs with two hours
of usefulness left and the setting is a fiction. This turns that from an assumption into a number.

Two spans, because they fail for different reasons and have different fixes:

- **found → scored** is the platform's own work: describe, embed, score. Slow here means a stage is
  backed up, and the queue depth in `libs.ops.health` says which one.
- **scored → delivered** is the wait for the user's next delivery slot. With one 15-minute cadence
  it should sit near seven minutes on average and never exceed fifteen by much; a larger number
  means the dispatcher is not keeping up, not that the pipeline is slow.

Reported as percentiles rather than an average. An average hides the tail, and the tail is what a
user actually experiences on the day the thing they wanted was late.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

# How far back to measure. Long enough that a quiet hour does not empty the sample, short enough
# that yesterday's incident stops flattering or damning today's number.
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


# The shape of both measurements. A constant with named slots rather than an f-string built at the
# call site: the slots are **column identifiers**, an identifier cannot be a bound parameter, and
# keeping the template in one place makes it obvious that nothing user-supplied reaches it.
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
    """Percentiles of `end - start`, in minutes, over rows where both are set.

    One statement rather than pulling rows into Python: the corpus is the largest table on the
    platform and this runs on an admin page load.
    """
    sql = _SPAN_SQL.format(start=start, end=end)
    row = session.execute(text(sql), {"hours": hours}).one()

    if not row.n:
        return LatencySpan(None, None, None, 0)
    return LatencySpan(round(float(row.p50), 1), round(float(row.p90), 1),
                       round(float(row.p99), 1), int(row.n))


def pipeline_latency(session: Session, *, hours: int = WINDOW_HOURS) -> dict:
    """Both spans, plus the total a user actually experiences.

    Each span carries its own verdict, so the page renders a judgement rather than asking every
    reader to remember what a good number looks like.
    """
    found_to_scored = _span(session, "c.first_seen_at", "s.scored_at", hours=hours).as_dict()
    scored_to_delivered = _span(session, "s.scored_at", "s.notified_at", hours=hours).as_dict()
    # What the person waited, end to end. Not the sum of the two above — the percentiles of a sum
    # are not the sum of the percentiles, and this is the one a user would recognise.
    found_to_delivered = _span(session, "c.first_seen_at", "s.notified_at", hours=hours).as_dict()

    return {
        "window_hours": hours,
        "found_to_scored": _judged("found_to_scored", found_to_scored),
        "scored_to_delivered": _judged("scored_to_delivered", scored_to_delivered),
        "found_to_delivered": _judged("found_to_delivered", found_to_delivered),
        # The worst of the three, so the page has one word for "is the pipeline healthy" without
        # re-deriving it and disagreeing with the rows underneath.
        "verdict": _worst([
            _judged("found_to_scored", found_to_scored)["verdict"],
            _judged("scored_to_delivered", scored_to_delivered)["verdict"],
            _judged("found_to_delivered", found_to_delivered)["verdict"],
        ]),
    }

# **Set from measured behaviour, not guessed.** Minutes, per span, as (amber, red).
#
# The product's whole claim is being early to a posting, so these are tight on purpose -- but they
# are also the number an operator will learn to ignore if it cries wolf, and this has already gone
# wrong once here: a found-to-scored median looked alarming and turned out to be measuring backlog
# age rather than pipeline latency. Anything that turns amber should be something a person would
# actually want to look at.
#
# Reasoning per span, so a future change to them is a change to an argument rather than a number:
#
# - found -> scored: the harvest runs every 15 minutes and the score lane drains behind it, so one
#   missed cycle is normal and two is a backlog worth seeing.
# - scored -> delivered: delivery is a 15-minute cadence and sending is paced, so a job scored just
#   after a send waits most of a cycle by design. An hour means something is not draining.
# - found -> delivered: not the sum of the other two -- it is what the user experiences, and it is
#   the only one of the three worth waking up for.
#
# **`posted_to_found` is deliberately absent, and that absence is the decision.** Sources round what
# they publish — LinkedIn reports "2 days ago" — so a threshold on it would be judging their
# rounding, and a red pill produced by rounding is exactly the colour an operator learns to ignore.
# It is reported without a verdict rather than dropped, because harvest lag is part of what a user
# waits and hiding it would make the judged rows look like the whole story.
THRESHOLDS: dict[str, tuple[int, int]] = {
    "found_to_scored": (30, 60),
    "scored_to_delivered": (30, 60),
    "found_to_delivered": (60, 120),
    # Per zone, for the drill-down. Each is one missed cycle of whatever drives that zone, with red
    # at the point where a person should stop reading and start looking.
    "found_to_described": (30, 60),
    # The embedder answers in milliseconds and drains continuously, so unlike the others this one
    # has no cadence to forgive: minutes here mean a queue, not a schedule.
    "described_to_embedded": (15, 45),
    "embedded_to_scored": (30, 60),
}

# Why each threshold is where it is, in the words the expanded row shows. **A colour whose reasoning
# lives only in a Python constant is a colour nobody trusts**, and this project has already had one
# number turn out to be measuring something other than what its label claimed.
THRESHOLD_REASONING: dict[str, str] = {
    "posted_to_found": "Not judged. Sources round what they report, so this measures their "
                       "precision as much as our lag.",
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

# Below this many samples a percentile is arithmetic rather than evidence, and colouring it invites
# somebody to chase a number that three rows produced.
MIN_SAMPLES = 20


def verdict(span: str, measured: dict) -> str:
    """`ok`, `warn`, `down`, or `unknown` for one span.

    Judged on p90 rather than the median: half the jobs arriving promptly is not the claim this
    product makes, and a median hides exactly the tail a candidate loses a role in.
    """
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
    """The most serious verdict present.

    `unknown` never outranks a real one: a span with too few samples should not turn the whole
    pipeline grey while another span is plainly on fire.
    """
    for level in ("down", "warn", "ok"):
        if level in verdicts:
            return level
    return "unknown"


# Per-lane timing, from the queue's own timestamps. Two numbers per lane, because they fail for
# different reasons and have different fixes: **waiting** means not enough workers or a lane behind
# a long job, **running** means the work itself got slower. A single "how long did it take" hides
# which, and they are fixed in completely different places.
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
    """How long each lane waited and then ran, in seconds.

    This is where an optimisation target actually comes from. The end-to-end spans say a posting
    took 36 minutes; they do not say whether that was a worker being busy or the work being slow,
    and those have nothing to do with each other.

    `run_max` is carried because a lane whose p90 is fine and whose worst case is four minutes has a
    tail worth looking at, and a percentile alone would never show it.
    """
    rows = session.execute(text(_LANE_SQL), {"hours": hours}).all()
    return [
        {"lane": row.lane, "samples": int(row.n),
         "wait_p50": round(float(row.wait_p50 or 0), 1),
         "wait_p90": round(float(row.wait_p90 or 0), 1),
         "run_p50": round(float(row.run_p50 or 0), 1),
         "run_p90": round(float(row.run_p90 or 0), 1),
         "run_max": round(float(row.run_max or 0), 1)}
        for row in rows]


# The end-to-end span, bucketed by hour. **A snapshot cannot show a spike**, and a spike is the
# thing worth chasing: a p90 of 36 minutes averaged over a day hides an hour where it was four.
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
#
# **A posting arrives as a link and leaves as a card, and crosses five boundaries in between.** Each
# boundary is somewhere work is picked up, done, and handed on, and each fails for its own reason
# with its own fix. The end-to-end number cannot name which one, so it cannot be acted on; these can
# (ROADMAP 6p).
#
# Two scopes, and the difference is not cosmetic. A `corpus` stage happens **once for everybody** —
# a description is fetched and a vector computed for the shared row — so it must be measured on
# `corpus` alone. A `user` stage happens **once per account**, so it joins `user_job_state`. Running
# a corpus stage through that join would multiply every posting by the number of accounts that hold
# it and report a fabricated sample count.


@dataclass(frozen=True)
class Stage:
    """One zone of the pipeline: what happens in it, and how to time it.

    Declarative rather than six near-identical functions, because the interesting differences
    between stages are exactly these fields and they should be readable side by side.
    """

    key: str
    label: str
    # Which service does the work. The first thing an operator needs after "this row is red".
    zone: str
    # What actually happens between the two timestamps, in a sentence.
    what: str
    scope: str  # "corpus" — once for everyone; "user" — once per account.
    start: str
    end: str
    # The queue lane that performs it, where one does. Harvest and describe run on timers rather
    # than the queue, so there is nothing to split into waiting and running for those.
    lane: str | None = None
    # A count of what is sitting at this zone's door, where that question has an answer. `since` is
    # the column that dates the wait, so the oldest one can be aged.
    backlog_where: str | None = None
    backlog_since: str | None = None
    # A caveat the expanded row shows above the numbers. Present when the measurement itself has a
    # limitation somebody would otherwise read past.
    caveat: str | None = None


# The pipeline, in order. `posted_to_found` is deliberately first and deliberately unjudged — see
# THRESHOLDS below for why it carries no colour.
STAGES: tuple[Stage, ...] = (
    Stage(
        key="posted_to_found",
        label="Posted → found",
        zone="harvester, every 15 minutes",
        what="The site published the posting and the harvest saw the link. This is the part of the "
             "wait that happens before the platform knows the job exists.",
        scope="corpus",
        start="c.posted_at",
        end="c.first_seen_at",
        caveat="Sources round what they report — LinkedIn says “2 days ago” — so this number's "
               "precision is theirs, not ours. Shown because harvest lag is genuinely part of what "
               "a user waits; unjudged because a colour produced by rounding is a colour nobody "
               "should trust.",
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
        backlog_where="c.description_fetched_at IS NULL AND c.trimmed_at IS NULL "
                      "AND c.closed_at IS NULL",
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
        backlog_where="c.description IS NOT NULL AND c.embedding IS NULL AND c.trimmed_at IS NULL",
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

# Where each scope's rows come from. The recency guard on `first_seen_at` belongs to both: without
# it a rescore of the whole corpus — which happens on every retrain — reports the age of every old
# row as pipeline latency.
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
    """Percentiles for one zone, in minutes, plus its worst case.

    `worst` is carried alongside the percentiles because a stage with a healthy p90 and a
    forty-minute tail has something worth looking at that no percentile would ever show.
    """
    sql = _STAGE_SQL.format(start=stage.start, end=stage.end, source=_SCOPE_FROM[stage.scope])
    row = session.execute(text(sql), {"hours": hours}).one()
    if not row.n:
        return {"p50": None, "p90": None, "p99": None, "worst": None, "samples": 0}
    return {"p50": round(float(row.p50), 1), "p90": round(float(row.p90), 1),
            "p99": round(float(row.p99), 1), "worst": round(float(row.worst), 1),
            "samples": int(row.n)}


def stage_breakdown(session: Session, *, hours: int = WINDOW_HOURS) -> list[dict]:
    """Every zone, in pipeline order, with the numbers a collapsed row shows.

    Deliberately only the percentiles and a verdict. The things that say *why* a zone is slow —
    waiting versus running, the backlog behind it, the shape of the last day — are a different kind
    of question and cost a query each, so they live in `stage_detail` behind an expansion rather
    than in twelve more columns on a page load.
    """
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
            # **`unjudged` is not `unknown`.** One means "we chose not to put a colour on this and
            # here is why"; the other means "there was not enough data to say". Collapsing them
            # would make a deliberate decision look like missing data.
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
    """One zone, bucketed by hour. **A snapshot cannot show a spike**, and the spike is the hour
    worth chasing: a p90 of 36 minutes averaged over a day hides the hour it was four times that."""
    sql = _STAGE_SERIES_SQL.format(start=stage.start, end=stage.end,
                                   source=_SCOPE_FROM[stage.scope])
    rows = session.execute(text(sql), {"hours": hours}).all()
    return [{"at": row.bucket.isoformat(), "samples": int(row.n),
             "p50": round(float(row.p50), 1) if row.p50 is not None else None,
             "p90": round(float(row.p90), 1) if row.p90 is not None else None}
            for row in rows]


def _stage_backlog(session: Session, stage: Stage) -> dict | None:
    """How much is sitting at this zone's door, and how long the oldest has waited.

    **The percentiles describe what got through; this describes what did not.** A stage with a fine
    p90 and four thousand rows queued behind it is about to stop having a fine p90, and only this
    number says so in advance.
    """
    if not stage.backlog_where or not stage.backlog_since:
        return None
    sql = _BACKLOG_SQL.format(since=stage.backlog_since, where=stage.backlog_where)
    row = session.execute(text(sql)).one()
    return {"waiting": int(row.n),
            "oldest_min": round(float(row.oldest_min), 1) if row.oldest_min is not None else None}


def stage_detail(session: Session, key: str, *, hours: int = WINDOW_HOURS) -> dict:
    """Everything an expanded row shows: why this zone, not just how slow.

    Four answers, and they are different in kind from a percentile, which is why they are here
    rather than on the table:

    - **waiting vs running**, where a queue lane does the work. `created → claimed` is waiting and
      means too few workers; `claimed → finished` is running and means the work itself got slower.
      One "how long did it take" hides which, and they are repaired in completely different places.
    - **the backlog**, which predicts the next hour where a percentile only describes the last.
    - **an hourly series**, so a spike has somewhere to appear.
    - **the threshold's reasoning**, in words, next to the number it judges.
    """
    stage = _BY_KEY.get(key)
    if stage is None:
        raise KeyError(key)

    lane = None
    if stage.lane:
        # One row out of the per-lane timings rather than a second query shape. Absent when the lane
        # completed nothing in the window, which is itself worth showing as "nothing ran".
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
