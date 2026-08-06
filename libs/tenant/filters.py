"""What one account will accept, as a set of cheap conditions applied before scoring.

**The scorer and the notifier ask the same question at two points**, and until now they answered it
separately: `services.notifier.eligibility` decided what could be *sent*, and nothing at all decided
what was worth *scoring*. So every account's model ran over every row in the corpus, and the
filtering happened afterwards — which is the wrong order, because the model call is the expensive
part and the filters are microseconds.

This is that question in one place, in `libs`, because both services need it and neither may import
the other (CLAUDE.md §5).

## The two layers, and why they are separate

1. **SQL** — country, freshness, closed. Indexed comparisons that never fetch the row, so the cost
   is paid by Postgres on columns built for it (`corpus.country`, `first_seen_at`). This is what
   makes a 30,000-row corpus cheap.
2. **In Python** — the user's own excluded words, against title and company. A `LIKE` per term per
   row would be a sequential scan; a substring check over the few hundred rows that survived layer
   one is nothing.

The centroid (`libs.scoring.centroid`) sits between them: it needs the embedding, so it runs on what
SQL returned, and it removes most of what is left.

## Unknown always passes

A posting with no country, no posted date, or no company is **kept**. That rule is the same
everywhere in this platform and it is not a nicety: 16% of rows carry no location at all, and a
filter that dropped them would quietly delete the most portable jobs on the board. A wrong exclusion
costs somebody a role and is invisible; a wrong inclusion costs one model call.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import true

from libs.contracts.config import migrate_config
from libs.corpus.geo import wanted_countries
from libs.db.models import CorpusJob

# The window an account gets if it never said, matching `SearchSection.posted_within_hours`.
#
# **Here rather than in `libs.ops.health`**, where it lived beside the count that reads it: the
# filters are what it configures, and health importing filters while filters imported health was a
# circular import waiting for the second caller (ROADMAP 6az).
DEFAULT_MAX_AGE_HOURS = 24


@dataclass(frozen=True)
class UserFilters:
    """One account's pre-score conditions, read from their config once per batch."""

    # `None` means "no country selection", which admits everywhere — never an empty set, because a
    # set that happens to be empty and a deliberate "anywhere" must not look the same.
    countries: frozenset[str] | None = None
    max_age_hours: int = DEFAULT_MAX_AGE_HOURS
    # Lower-cased terms the user typed in order *not* to see them, matched against title and
    # company. "senior", "unpaid", a company they will not work for.
    exclude: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        # Compiled once per batch rather than per row: one pass over the title and company against
        # every term at once, which is what keeps this affordable inside the scoring loop.
        pattern = "|".join(re.escape(term) for term in self.exclude if term)
        object.__setattr__(
            self, "_matcher",
            re.compile(rf"(?<!\w)({pattern})(?!\w)", re.I) if pattern else None)

    @classmethod
    def from_config(cls, config: dict | None) -> UserFilters:
        """Build from a stored `user_config.config`.

        **Migrated before it is read.** A v1 config keeps `posted_within_hours` and
        `exclude_keywords` at the top level while this reads sections; without the migration a v1
        user silently gets the defaults instead of their own answers, which happened live on
        2026-07-30.
        """
        config = migrate_config(config or {}) if config else {}
        sections = config.get("sections") or []

        # **The widest window across their sections, not the narrowest.** A user with six hours on
        # one search and a week on another asked for both, and a posting cannot be attributed back
        # to the section that found it — so the generous reading is the only one that cannot
        # silently delete something they asked for.
        windows = [int(section.get("posted_within_hours") or 0) for section in sections]

        # **Locations live inside sections, not at the top level**, and `migrate_config` moves a
        # v1 top-level list into one. Reading the top level returned nothing for every real config
        # — the same shape of mistake that made `union_keywords` fetch every board's unfiltered
        # feed while the platform behaved as though nobody had signed up (ROADMAP 6l).
        #
        # The union across sections, for the reason the window takes the widest: a user with the EU
        # on one search and the UK on another asked for both, and a posting cannot be attributed
        # back to the section that found it.
        places = [place for section in sections for place in section.get("locations") or []]

        return cls(
            countries=wanted_countries(places),
            max_age_hours=max([w for w in windows if w > 0], default=DEFAULT_MAX_AGE_HOURS),
            exclude=tuple(
                term.strip().lower()
                for section in sections
                for term in section.get("exclude_keywords") or []
                if term and term.strip()),
        )

    def fresh_since(self, *, now: datetime | None = None) -> datetime:
        """The oldest a posting may be and still be worth scoring for this account."""
        return (now or datetime.now(UTC)) - timedelta(hours=self.max_age_hours)

    def excluded(self, title: str | None, company: str | None) -> str | None:
        """The term that rules this posting out, or `None`.

        Returns *which* term rather than a boolean so the caller can log what a filter is doing —
        "dropped 400 for 'senior'" is a config problem somebody can fix in a minute, and "dropped
        400" is not.

        **Whole words, not substrings, and this was a live defect.** A plain `term in haystack` is
        what both this and the delivery filter did, and one live account excludes `"sr"` — which
        matched *Israel* Tech and Di*sr*aeli Ltd and silently removed them from that feed. A user
        typing a short exclusion means the word, and nothing else can be assumed from two letters
        (ROADMAP 6av).

        Multi-word terms work unchanged: `\bquick hire staffing\b` is one alternative like any
        other, and `re.escape` keeps `jack & jill` literal.
        """
        if self._matcher is None:
            return None
        found = self._matcher.search(f"{title or ''} {company or ''}")
        return found.group(0).lower() if found else None


def candidate_conditions(filters: UserFilters, *, now: datetime | None = None) -> list:
    """The SQL every "is this row a candidate for this account" question must ask.

    **Three callers asked it and three copies drifted.** `pending_feed` decides what to send,
    `pending_matches` decides whether to page an operator, and `score_lane` decides what to score —
    and on 2026-08-06 the first was fixed and the other two were not, so the health page counted 155
    rows the notifier could not see and paged about all of them (ROADMAP 6az).

    Returns conditions rather than a query because the three select different columns and order
    differently; what they must not differ on is *which rows qualify*.
    """
    fresh = filters.fresh_since(now=now)
    return [
        # Nothing to apply for.
        CorpusJob.closed_at.is_(None),
        # **Dated rows against their posted time, undated ones against when we found them.** An
        # undated row used to be admitted unconditionally, on the reasoning that a later pass would
        # date it — which needs the account's own proxy, so an account without one accumulated rows
        # that were re-judged every cycle forever (ROADMAP 6ay).
        (CorpusJob.posted_at >= fresh)
        | (CorpusJob.posted_at.is_(None) & (CorpusJob.first_seen_at >= fresh)),
        # Their countries, or a posting that names no readable place. NULL passes: 16% of rows
        # carry no country and they are disproportionately the remote-first ones.
        true() if not filters.countries
        else CorpusJob.country.is_(None) | CorpusJob.country.in_(filters.countries),
    ]
