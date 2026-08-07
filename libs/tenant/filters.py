"""What one account will accept, as a set of cheap conditions applied before scoring."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, true

from libs.contracts.config import migrate_config
from libs.corpus import langfilter, workfilter
from libs.corpus.geo import wanted_countries
from libs.corpus.ranking import applicant_glut
from libs.db.models import CorpusJob
from libs.limits import limit_default

# The catalogue's ceiling, for a caller with no user to resolve against — a test, or a direct construction.
CATALOGUE_APPLICANT_CAP = limit_default("applicant_cap")


@dataclass(frozen=True)
class UserFilters:
    """One account's pre-score conditions, read from their config once per batch."""

    # `None` means no country selection and admits everywhere; an empty set must not look the same.
    countries: frozenset[str] | None = None
    # The operator's `job_max_age_hours`, passed in. Never defaulted here: a window nobody set is a
    # window nobody can change, and this one decides how long a scored job stays sendable.
    max_age_hours: int = 0
    # Lower-cased terms the user typed in order *not* to see them, matched against title and company.
    exclude: tuple[str, ...] = field(default_factory=tuple)
    # Everything below used to live in `services.notifier.eligibility` and run at send time.
    applicant_cap: int = 0
    onsite_locations: tuple[str, ...] = field(default_factory=tuple)
    languages: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        # Compiled once per batch: one pass over title and company against every term at once.
        pattern = "|".join(re.escape(term) for term in self.exclude if term)
        object.__setattr__(
            self, "_matcher",
            re.compile(rf"(?<!\w)({pattern})(?!\w)", re.I) if pattern else None)

    @classmethod
    def from_config(cls, config: dict | None, *, max_age_hours: int,
                    applicant_cap: int | None = CATALOGUE_APPLICANT_CAP) -> UserFilters:
        """Build from a stored `user_config.config`."""
        config = migrate_config(config or {}) if config else {}
        sections = config.get("sections") or []

        # Locations live inside sections, and `migrate_config` moves a v1 top-level list into one.
        places = [place for section in sections for place in section.get("locations") or []]

        return cls(
            countries=wanted_countries(places),
            max_age_hours=int(max_age_hours),
            exclude=tuple(
                term.strip().lower()
                for section in sections
                for term in section.get("exclude_keywords") or []
                if term and term.strip()),
            # An operator ceiling rather than a search preference, so it comes from `libs.limits`.
            applicant_cap=0 if applicant_cap is None else int(applicant_cap),
            onsite_locations=tuple(workfilter.onsite_locations(config.get("onsite_locations"))),
            # An empty set means no opinion and admits every language.
            languages=frozenset(str(code).lower() for code in config.get("languages") or []),
        )

    def fresh_since(self, *, now: datetime | None = None) -> datetime:
        """The oldest a posting may be and still be worth scoring for this account."""
        return (now or datetime.now(UTC)) - timedelta(hours=self.max_age_hours)

    def excluded(self, title: str | None, company: str | None) -> str | None:
        """The term that rules this posting out, or `None`."""
        if self._matcher is None:
            return None
        found = self._matcher.search(f"{title or ''} {company or ''}")
        return found.group(0).lower() if found else None


    def unwanted_for_training(self, row) -> str | None:
        """Why this posting is not worth buying a label for, or `None`.

        A narrower question than `unwanted`. A label is an example of what this person wants, so the
        checks that describe the *posting's* content apply — the language they cannot read, the words
        they never want to see. The ones that describe its *circumstances* do not: how many people
        have applied, whether it is on site this week, and how old it is say nothing about whether
        it is a good example, and filtering on them would thin the training set for no gain.
        """
        if self.excluded(row.get("title"), row.get("company")):
            return "excluded_keyword"
        if not langfilter.is_allowed(row.get("language"), set(self.languages),
                                     source=row.get("source")):
            return "language"
        return None

    def unwanted(self, row) -> str | None:
        """Why this account can never receive this posting, or `None`."""
        # Title and company only, never the description.
        if self.excluded(row.get("title"), row.get("company")):
            return "excluded_keyword"

        if applicant_glut(source=row.get("source"), applicants=row.get("applicants"),
                          cap=self.applicant_cap):
            return "applicant_glut"

        # `explicit=` is the tag parsed off the posting's own page, and it beats every heuristic.
        mode = workfilter.classify(row.get("title") or "", row.get("location") or "",
                                   row.get("description"), row.get("is_remote"),
                                   explicit=row.get("workplace"))
        if not workfilter.is_allowed(mode, row.get("location") or "", list(self.onsite_locations)):
            return "workplace"

        # The stored code, detected once at describe time rather than per reader per pass.
        if not langfilter.is_allowed(row.get("language"), set(self.languages),
                                     source=row.get("source")):
            return "language"

        return None


def filters_for(session, user) -> UserFilters:
    """This account's pre-score conditions, with the operator's numbers already resolved.

    One construction site for the three lanes that ask the same question, so the ceiling an operator
    sets and the window they set arrive together rather than one of them being forgotten.
    """
    from libs import limits, settings  # noqa: PLC0415 - avoids a cycle at import time
    from libs.db.models import UserConfig  # noqa: PLC0415

    cap = limits.resolve(user, "applicant_cap")
    return UserFilters.from_config(
        session.scalar(select(UserConfig.config).where(UserConfig.user_id == user.id)),
        max_age_hours=int(settings.get(session, "job_max_age_hours")),
        applicant_cap=None if cap is None else int(cap))


def candidate_conditions(filters: UserFilters, *, now: datetime | None = None) -> list:
    """The SQL every "is this row a candidate for this account" question must ask."""
    fresh = filters.fresh_since(now=now)
    return [
        # Nothing to apply for.
        CorpusJob.closed_at.is_(None),
        # Dated rows against their posted time, undated ones against when we found them.
        (CorpusJob.posted_at >= fresh)
        | (CorpusJob.posted_at.is_(None) & (CorpusJob.first_seen_at >= fresh)),
        # Their countries, or a posting that names no readable place.
        true() if not filters.countries
        else CorpusJob.country.is_(None) | CorpusJob.country.in_(filters.countries),
    ]
