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
    """Both spans, plus the total a user actually experiences."""
    return {
        "window_hours": hours,
        "found_to_scored": _span(session, "c.first_seen_at", "s.scored_at", hours=hours).as_dict(),
        "scored_to_delivered": _span(session, "s.scored_at", "s.notified_at",
                                     hours=hours).as_dict(),
        # What the person waited, end to end. Not the sum of the two above — the percentiles of a
        # sum are not the sum of the percentiles, and this is the one a user would recognise.
        "found_to_delivered": _span(session, "c.first_seen_at", "s.notified_at",
                                    hours=hours).as_dict(),
    }
