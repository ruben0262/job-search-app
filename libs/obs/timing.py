"""How long each stage took, and where the time actually goes.

**"The feed is slow" is not a diagnosis.** A posting travels through harvest, dedup, description
fetch, embed, score, filter and deliver before anybody sees it, and any one of those can be the
thing costing ten minutes. Without per-stage numbers the only available action is to guess, and
the stage everybody guesses is the model call — which on this platform is frequently not the one.

Two things are recorded, and the difference between them matters:

- **Stage duration** — how long one step took inside one process. Answers "which part is slow".
- **End-to-end latency** — from a posting first being seen to it reaching a user. Answers "how
  fresh is the feed", which is the number the product is actually judged on, and which no single
  stage's duration predicts because most of that time is spent *waiting between* stages.

The second is the one worth watching. A cycle whose stages are all fast and which runs every
fifteen minutes still delivers a job that is, on average, seven and a half minutes stale before
anything else has happened.

## Written as logs, not metrics

One `stage.timed` line per stage, in the same greppable `key=value` shape `audit` uses. There is no
metrics backend on this box and adding one to answer a timing question would be building the
observability stack instead of using it — `journalctl | grep stage.timed` answers this today, and
the line is already in the shape a collector would want if one ever arrives.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from libs.obs.logs import audit

logger = logging.getLogger(__name__)


@dataclass
class Timings:
    """Stage durations for one run, in milliseconds, in the order they happened.

    Ordered rather than a plain dict because "what ran, in what sequence, for how long" is the
    question — a stage that is fast but runs three times is a different problem from one that is
    slow once, and an unordered mapping hides the difference.
    """

    stages: list[tuple[str, float]] = field(default_factory=list)

    def record(self, stage: str, milliseconds: float) -> None:
        self.stages.append((stage, milliseconds))

    @property
    def total_ms(self) -> float:
        return sum(ms for _stage, ms in self.stages)

    def slowest(self) -> tuple[str, float] | None:
        """The stage that cost the most. What a one-line summary should name."""
        return max(self.stages, key=lambda item: item[1], default=None)

    def as_fields(self) -> dict[str, int]:
        """`{stage_ms: int}` for an audit line. Rounded — sub-millisecond precision on a stage
        measured in seconds is noise that makes two runs look different when they are not."""
        fields = {f"{stage}_ms": round(ms) for stage, ms in self.stages}
        fields["total_ms"] = round(self.total_ms)
        return fields


@contextmanager
def timed(stage: str, timings: Timings | None = None, *,
          log: logging.Logger | None = None) -> Iterator[None]:
    """Time one stage. Records it, and logs it even when the stage raises.

    **The failure path is timed too**, which is not a detail: a stage that takes ninety seconds and
    then throws is the most useful timing there is, and a naive `try`-less context manager loses
    exactly that one. `perf_counter` rather than wall clock, so an NTP step mid-cycle cannot report
    a negative duration.
    """
    started = time.perf_counter()
    failed = False
    try:
        yield
    except BaseException:
        failed = True
        raise
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000
        if timings is not None:
            timings.record(stage, elapsed_ms)
        audit(log or logger, "stage.timed", stage=stage, ms=round(elapsed_ms),
              outcome="failed" if failed else "ok")


def report(timings: Timings, *, event: str, log: logging.Logger | None = None, **fields) -> None:
    """One line for the whole run: every stage, the total, and which stage dominated.

    The slowest stage is named explicitly rather than left to be worked out from the other numbers,
    because the person reading this is reading it at the moment something is slow.
    """
    slowest = timings.slowest()
    audit(log or logger, event,
          **timings.as_fields(),
          slowest=slowest[0] if slowest else "none",
          slowest_ms=round(slowest[1]) if slowest else 0,
          **fields)
