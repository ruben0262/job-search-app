"""What one account will accept, as a set of cheap conditions applied before scoring."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select, true

from libs.contracts.config import migrate_config
from libs.corpus import langfilter, workfilter
from libs.corpus.geo import wanted_countries
from libs.corpus.ranking import applicant_glut
from libs.corpus.repository import DESCRIBE_MAX_FAILURES
from libs.db.models import CorpusJob
from libs.limits import limit_default
from libs.settings import DEFAULT_SOURCE_MIN_AGE_HOURS, SOURCE_MIN_AGE_HOURS

# The catalogue's ceiling, for a caller with no user to resolve against — a test, or a direct construction.
CATALOGUE_APPLICANT_CAP = limit_default("applicant_cap")


# What each answer to "how often would you go in" accepts. Where a posting is, is the country filter's job.
_ACCEPTS = {"remote_only": {"remote"}, "remote_or_hybrid": {"remote", "hybrid"},
            "any": {"remote", "hybrid", "onsite"}}


def _widest_policy(sections: list[dict]) -> str:
    """The most permissive policy across sections, so no section narrows another one's feed."""
    asked = {section.get("remote_policy") or "remote_or_hybrid" for section in sections} or {"any"}
    return next(policy for policy in ("any", "remote_or_hybrid", "remote_only") if policy in asked)


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
    # Their answer to "how often would you go in": remote_only, remote_or_hybrid or any.
    remote_policy: str = "any"
    languages: frozenset[str] = field(default_factory=frozenset)
    # How long a posting from each source stays deliverable. The operator's, via platform
    # settings, with the measured map as the default — `filters_for` resolves it. Held here rather
    # than read inside `candidate_conditions` so the SQL builder stays free of a session.
    source_min_ages: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        # Compiled once per batch: one pass over title and company against every term at once.
        pattern = "|".join(re.escape(term) for term in self.exclude if term)
        object.__setattr__(
            self, "_matcher",
            re.compile(rf"(?<!\w)({pattern})(?!\w)", re.I) if pattern else None)

    @classmethod
    def from_config(cls, config: dict | None, *, max_age_hours: int,
                    applicant_cap: int | None = CATALOGUE_APPLICANT_CAP,
                    source_min_ages: tuple[tuple[str, int], ...] = ()) -> UserFilters:
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
            remote_policy=_widest_policy(sections),
            # An empty set means no opinion and admits every language.
            languages=frozenset(str(code).lower() for code in config.get("languages") or []),
            source_min_ages=source_min_ages,
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
        # Unknown is kept under every policy: absence of a signal is not evidence of on-site.
        if mode and mode not in _ACCEPTS[self.remote_policy]:
            return "workplace"

        # The stored code, detected once at describe time rather than per reader per pass.
        if not langfilter.is_allowed(row.get("language"), set(self.languages),
                                     source=row.get("source")):
            return "language"

        return None


def _fresh_enough(filters: UserFilters, *, now: datetime | None = None):
    """Whether a posting is recent enough for this account, judged per source.

    **One global window does not mean "fresher jobs", it means "LinkedIn only".** Measured against
    the live corpus on 2026-08-09 with the operator's window at six hours, fifteen of seventeen
    sources returned nothing — a board that publishes a few times a day has nothing posted in the
    last six hours, so the window was switching sources off rather than raising the bar. Each
    source is therefore given at least the window it can actually serve (`min_age_hours`), and the
    operator's setting applies on top of that rather than underneath it.

    The second half is about which clock to read. Most sources report a real posting time and are
    judged on it. A few report a date only — every `posted_at` at midnight — so by mid-morning
    their whole catalogue looks half a day old however fresh it is. Those are judged on
    `first_seen_at` instead, which is trustworthy here because the harvest runs every fifteen
    minutes and every ATS token is polled every cycle: when we first saw a posting is a better
    statement about its age than a date stamped to midnight.
    """
    reference = now or datetime.now(UTC)
    floors = dict(filters.source_min_ages) or SOURCE_MIN_AGE_HOURS
    per_source = case(
        *[(CorpusJob.source == source,
           reference - timedelta(hours=max(hours, filters.max_age_hours)))
          for source, hours in sorted(floors.items())],
        else_=reference - timedelta(
            hours=max(DEFAULT_SOURCE_MIN_AGE_HOURS, filters.max_age_hours)))

    # A midnight timestamp is a date, not a time. Judge those on when we found the row.
    date_only = func.date_trunc("day", CorpusJob.posted_at) == CorpusJob.posted_at
    return (
        (CorpusJob.posted_at.is_(None) & (CorpusJob.first_seen_at >= per_source))
        | (date_only & (CorpusJob.first_seen_at >= per_source))
        | (CorpusJob.posted_at.is_not(None) & ~date_only & (CorpusJob.posted_at >= per_source)))


def too_old_to_send(filters: UserFilters, *, now: datetime | None = None):
    """A posting that is no longer a candidate, for the sweep that gives up on unsent matches.

    **The negation of the admission rule, not a rule of its own**, and that is the whole point. The
    retirement sweep used to read a flat `job_max_age_hours` against
    `coalesce(posted_at, first_seen_at)` while carrying a comment claiming it read "the same clock
    the pre-score filter reads". It did not, in two ways: no per-source floor, and no handling of a
    date-only `posted_at`.

    What that cost, measured on the live platform 2026-08-11: **13 Adzuna matches destroyed an
    average of two minutes after the posting was first seen.** Adzuna's floor is 48 hours and
    `hiringcafe`'s is 168, so the filter admitted those postings on purpose; the sweep then judged
    them at the operator's 6 and retired them before the notifier could send them. Every source
    except LinkedIn has a floor above 6 hours, so this reached every account.

    Whether a given match survived was a race: delivery applies no age filter at all, so if the
    score lane's own notify job ran first the posting went out — 27 hours old and fine — and if the
    sweep ran first the same row was destroyed.
    """
    return ~_fresh_enough(filters, now=now)


def pending_match_conditions(cutoff: float) -> list:
    """Matches this account is actually still owed: scored above their cutoff and not given up on.

    One definition, because there were two. The notifier requires `retired_at IS NULL` — a match
    that aged past the window while waiting is one it deliberately abandoned, and sending it late
    is worse than not sending it. The status page asked the same question without that clause, so
    it counted work nobody would ever do: on 2026-08-08 an account showed "99 matches waiting" and
    a delivery warning, and **all 99 were retired**. The notifier was right and idle; the page was
    wrong and amber.
    """
    from libs.db.models import UserJobState  # noqa: PLC0415 - avoids a cycle at import time

    return [
        UserJobState.notified_at.is_(None),
        # The clause whose absence produced the phantom.
        UserJobState.retired_at.is_(None),
        UserJobState.score.is_not(None),
        UserJobState.score >= cutoff,
    ]


@dataclass(frozen=True)
class PlatformWindows:
    """The two operator settings every account's filters are built from.

    Identical for every account by definition — they are platform settings — and `filters_for` read
    both from the database **once per account**. In a loop over every account that is 2N queries
    returning the same two answers. Measured at fifty accounts: 351 queries for one health check.

    Read once with `platform_windows` and passed down. Not a cache: nothing is stored and nothing
    expires, so there is no second copy to go stale (CLAUDE.md §1).
    """

    max_age_hours: int
    source_min_ages: tuple[tuple[str, int], ...]


def platform_windows(session) -> PlatformWindows:
    """Read the platform-wide filter settings once, for a caller about to loop over accounts."""
    from libs import settings  # noqa: PLC0415 - avoids a cycle at import time

    return PlatformWindows(
        max_age_hours=int(settings.get(session, "job_max_age_hours")),
        source_min_ages=tuple(sorted(settings.source_min_ages(session).items())))


def filters_for(session, user, *, windows: PlatformWindows | None = None) -> UserFilters:
    """This account's pre-score conditions, with the operator's numbers already resolved.

    One construction site for the three lanes that ask the same question, so the ceiling an operator
    sets and the window they set arrive together rather than one of them being forgotten.

    `windows` is the platform half, for a caller looping over accounts: pass it and the two settings
    are read once rather than once per account. Omit it and it is read here, so a caller with one
    account stays a one-liner.
    """
    from libs import limits  # noqa: PLC0415 - avoids a cycle at import time
    from libs.db.models import UserConfig  # noqa: PLC0415

    windows = windows or platform_windows(session)
    # Kept next to the `None` that honours Unlimited: `test_unlimited_is_honoured_wherever_it_is
    # _offered` reads the few lines after a `resolve(...)` looking for it, and putting anything
    # between the two hides the check from the guard as well as from a reader.
    cap = limits.resolve(user, "applicant_cap")
    return UserFilters.from_config(
        session.scalar(select(UserConfig.config).where(UserConfig.user_id == user.id)),
        max_age_hours=windows.max_age_hours,
        applicant_cap=None if cap is None else int(cap),
        source_min_ages=windows.source_min_ages)


def candidate_conditions(filters: UserFilters, *, now: datetime | None = None) -> list:
    """The SQL every "is this row a candidate for this account" question must ask."""
    return [
        # Nothing to apply for.
        CorpusJob.closed_at.is_(None),
        _fresh_enough(filters, now=now),
        # Their countries, or a posting that names no readable place.
        true() if not filters.countries
        else CorpusJob.country.is_(None) | CorpusJob.country.in_(filters.countries),
        # A posting whose page refused to answer twice was abandoned, and its stored "description"
        # is its own title. Scoring a user's model against a sentence is worse than not scoring.
        CorpusJob.describe_failures < DESCRIBE_MAX_FAILURES,
    ]
