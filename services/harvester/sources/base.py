"""The shape every source adapter yields. Lifted from `pipeline/sources/base.py`.

Each adapter's `gather()` returns `JobLink`s, so the harvest loop's dedup and upsert logic is
source-agnostic — adding a board is a file, not a refactor.

**One change from the reference:** `as_new_link()` is gone. It produced the `new_links.json` object
shape, and that file does not exist here — rows go straight into the shared corpus through
`CorpusRepository`. Keeping a serialiser for a store that no longer exists would be an invitation to
reintroduce it.

`snippet` still carries up to 4,000 characters of description. The reference discovered that trusting
a source's own snippet, when it is long enough, avoids a second fetch per job — which is the
difference between a 15-minute cycle and an hour, and on the proxied tier it is also the difference
in bandwidth cost.
"""
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
    # LinkedIn only: True = 1-click on LinkedIn, False = an external apply link. The external case
    # is what makes the apply-URL dedup layer resolvable (libs/corpus §find_duplicate).
    easy_apply: bool | None = None
    application_url: str | None = None
    found_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def as_corpus_row(self) -> dict[str, Any]:
        """Keyword arguments for `CorpusRepository.add()`.

        Deliberately narrow: the corpus takes what identifies and describes a *public posting* and
        nothing about who asked for it. A source adapter has no access to a user and must not
        acquire one.
        """
        return {
            "url": self.url,
            "source": self.source,
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "is_remote": self.is_remote,
            # **These two were collected and then dropped here.** `date_posted` is why every feed
            # card was rendered without an age on it, and `easy_apply` is a ⚡ on the card and the
            # signal that makes the apply-URL dedup layer resolvable. Both were already on the
            # dataclass; this line is the whole fix.
            "posted_at": parse_date(self.date_posted),
            # Already recorded when the adapter parsed this link out of a response, and until now
            # thrown away at the corpus boundary — the same shape as `date_posted` and `easy_apply`
            # before them.
            "fetched_at": parse_date(self.found_at),
            "easy_apply": self.easy_apply,
            # Inferred from what the source gave us. The authoritative value — LinkedIn's own
            # workplace pill — arrives later with the description fetch and overwrites this.
            "workplace": classify(self.title, self.location, self.snippet, self.is_remote),
        }
