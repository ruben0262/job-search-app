"""The shape every source adapter yields."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from libs.corpus.filters import parse_date
from libs.corpus.workfilter import classify
from libs.settings import PLATFORM_TICK_MIN


def turn(count: int, per_run: int, *, now: float | None = None) -> range:
    """Which of `count` things (pages of a list in no date order, company boards) this run reads, when a
    run reads at most `per_run`: the next `per_run` of them, so each is read once every few runs. The run
    is counted on the platform's own clock (`PLATFORM_TICK_MIN`), so nothing is stored and no second clock
    is added; a run that did not happen leaves its share to the next round."""
    per_run = max(1, per_run)
    if count <= per_run:
        return range(count)
    run = int((now if now is not None else time.time()) // (PLATFORM_TICK_MIN * 60))
    start = (run % math.ceil(count / per_run)) * per_run
    return range(start, min(start + per_run, count))


@dataclass
class JobLink:
    """One posting as a source reported it, before dedup, filtering, or description fetch."""

    url: str
    title: str = ""
    company: str = ""
    location: str = ""
    source: str = ""
    date_posted: str | None = None      # ISO date string, or None if the source does not say
    snippet: str = ""
    is_remote: bool | None = None
    # LinkedIn only: True = 1-click on LinkedIn, False = an external apply link.
    easy_apply: bool | None = None
    application_url: str | None = None
    found_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    # The search term that returned this link. Not the posting's title: on LinkedIn the two
    # are often unrelated, and that difference is the thing nobody could see.
    found_via: str = ""

    def as_corpus_row(self) -> dict[str, Any]:
        """Keyword arguments for `CorpusRepository.add()`."""
        return {
            "url": self.url,
            "source": self.source,
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "is_remote": self.is_remote,
            # These two were collected and then dropped here.
            "posted_at": parse_date(self.date_posted),
            # Recorded when the adapter parsed this link, and thrown away at the corpus edge until now.
            "fetched_at": parse_date(self.found_at),
            "easy_apply": self.easy_apply,
            # Inferred from what the source gave us.
            "workplace": classify(self.title, self.location, self.snippet, self.is_remote),
            # How it was found, carried through to the row rather than dropped here.
            "discovered_via": self.found_via or None,
        }
