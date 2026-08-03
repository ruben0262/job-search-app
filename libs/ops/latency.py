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
THRESHOLDS: dict[str, tuple[int, int]] = {
    "found_to_scored": (30, 60),
    "scored_to_delivered": (30, 60),
    "found_to_delivered": (60, 120),
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
