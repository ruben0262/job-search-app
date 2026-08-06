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
from libs.corpus import langfilter, workfilter
from libs.corpus.geo import wanted_countries
from libs.corpus.ranking import applicant_glut
from libs.db.models import CorpusJob
from libs.limits import limit_default

# The window an account gets if it never said, matching `SearchSection.posted_within_hours`.
#
# **Here rather than in `libs.ops.health`**, where it lived beside the count that reads it: the
# filters are what it configures, and health importing filters while filters imported health was a
# circular import waiting for the second caller (ROADMAP 6az).
DEFAULT_MAX_AGE_HOURS = 24

# The catalogue's ceiling, for a caller with no user to resolve against — a test, or a direct
# construction. **A default argument rather than a `None` check inside**, because `None` already
# means something else here: it is the operator having chosen Unlimited. Reading "not supplied" and
# "unlimited" as the same value is how an account set to Unlimited silently ran at the default,
# which is the defect `tests/test_limits_are_wired.py` exists to catch — and did catch, here.
CATALOGUE_APPLICANT_CAP = limit_default("applicant_cap")


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
    # **Everything below used to live in `services.notifier.eligibility` and run at send time.**
    # That was one filter too late and one place too many: a posting the account could never
    # receive was still embedded against their model, scored, counted as "waiting" by the health
    # page and by the radar, and then silently dropped in the last second before sending. One
    # account sat on a permanent "1 job waiting, last delivery 2h ago" alert for exactly that
    # reason — the count and the send disagreed, and the count was the one an operator read.
    #
    # 0 means no ceiling, which is what Unlimited resolves to. **Not `None`**: `applicant_glut`
    # reads 0 as off, and two spellings of "no limit" is how one of them ends up wrong.
    applicant_cap: int = 0
    onsite_locations: tuple[str, ...] = field(default_factory=tuple)
    languages: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        # Compiled once per batch rather than per row: one pass over the title and company against
        # every term at once, which is what keeps this affordable inside the scoring loop.
        pattern = "|".join(re.escape(term) for term in self.exclude if term)
        object.__setattr__(
            self, "_matcher",
            re.compile(rf"(?<!\w)({pattern})(?!\w)", re.I) if pattern else None)

    @classmethod
    def from_config(cls, config: dict | None, *,
                    applicant_cap: int | None = CATALOGUE_APPLICANT_CAP) -> UserFilters:
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
            # An operator ceiling rather than a search preference, so it comes from `libs.limits`
            # and not from the config. `None` is Unlimited and becomes 0, which is what
            # `applicant_glut` reads as "drop nothing".
            applicant_cap=0 if applicant_cap is None else int(applicant_cap),
            onsite_locations=tuple(workfilter.onsite_locations(config.get("onsite_locations"))),
            # **An account that never answered must not have its whole feed dropped**, so an empty
            # set means "no opinion" and admits every language.
            languages=frozenset(str(code).lower() for code in config.get("languages") or []),
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


    def unwanted(self, row) -> str | None:
        """Why this account can never receive this posting, or `None`.

        **The whole of "would we ever send this", in one place, applied before it is scored.** It
        used to run at delivery, which meant every posting an account could not receive was still
        embedded against their model, scored, and counted as waiting by two pages that then
        disagreed with what was sent.

        Returns the reason rather than a boolean so the caller can tally them: "dropped 51 for
        applicant_glut" is a setting somebody can change in a minute, and "dropped 51" is not.

        **Every check fails open.** An undetectable language, an unclassifiable workplace and an
        unknown applicant count all keep the posting: a wrong exclusion costs somebody a role and is
        invisible, while a wrong inclusion costs one model call.
        """
        # Title and company only, never the description. A posting that merely *mentions* a term in
        # a benefits paragraph is not the job the user was excluding, and matching the body turned
        # "no recruitment agencies" into a filter that removed most of a feed.
        if self.excluded(row.get("title"), row.get("company")):
            return "excluded_keyword"

        if applicant_glut(source=row.get("source"), applicants=row.get("applicants"),
                          cap=self.applicant_cap):
            return "applicant_glut"

        # `explicit=` is the tag parsed off the posting's own page, and it wins over every
        # heuristic: it is what the employer wrote, not what the title implies.
        mode = workfilter.classify(row.get("title") or "", row.get("location") or "",
                                   row.get("description"), row.get("is_remote"),
                                   explicit=row.get("workplace"))
        if not workfilter.is_allowed(mode, row.get("location") or "", list(self.onsite_locations)):
            return "workplace"

        # Language last: it is the only check that runs a detector, and the cheap checks above have
        # already removed most of what it would have been run over.
        if not langfilter.is_allowed(row.get("description") or "", set(self.languages),
                                     source=row.get("source")):
            return "language"

        return None


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
