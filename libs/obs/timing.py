"""How long each stage took, and where the time actually goes."""
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
    """Stage durations for one run, in milliseconds, in the order they happened."""

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
        """`{stage_ms: int}` for an audit line."""
        fields = {f"{stage}_ms": round(ms) for stage, ms in self.stages}
        fields["total_ms"] = round(self.total_ms)
        return fields


@contextmanager
def timed(stage: str, timings: Timings | None = None, *,
          log: logging.Logger | None = None) -> Iterator[None]:
    """Time one stage."""
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
    """One line for the whole run: every stage, the total, and which stage dominated."""
    slowest = timings.slowest()
    audit(log or logger, event,
          **timings.as_fields(),
          slowest=slowest[0] if slowest else "none",
          slowest_ms=round(slowest[1]) if slowest else 0,
          **fields)
