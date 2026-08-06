"""The shape every source adapter yields."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from libs.corpus.filters import parse_date
from libs.corpus.workfilter import classify


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
        }
