"""What one account will accept, as a set of cheap conditions applied before scoring."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from libs.contracts.config import migrate_config
from libs.corpus import langfilter, workfilter
from libs.corpus.geo import wanted_countries
from libs.corpus.repository import DESCRIBE_MAX_FAILURES, TYPED
from libs.db.models.corpus import WORKPLACES
from libs.limits import limit_default
from libs.settings import DEFAULT_SOURCE_MIN_AGE_HOURS, SOURCE_MIN_AGE_HOURS

# The catalogue's ceiling, for a caller with no user to resolve against — a test, or a direct construction.
CATALOGUE_APPLICANT_CAP = limit_default("applicant_cap")


def _workplaces(sections: list[dict]) -> frozenset[str]:
    """Every way of working switched on in any section, so no section narrows another one's feed. A
    section with none switched on narrows nothing. Where a posting is, is the country filter's job."""
    switched = [set(section.get("workplaces") or WORKPLACES) for section in sections]
    return frozenset().union(*switched) if switched else frozenset(WORKPLACES)


def applicant_glut(*, source: str | None, applicants: int | None, cap: int) -> bool:
    """Whether to drop a listing as over-subscribed. Only LinkedIn reports a count worth trusting."""
    if cap <= 0 or not isinstance(applicants, int):
        return False
    return (source or "").lower() == "linkedin" and applicants >= cap


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
    # The ways of working they switched on: remote, hybrid, onsite. All three is anything.
    workplaces: frozenset[str] = frozenset(WORKPLACES)
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
            workplaces=_workplaces(sections),
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


    def unwanted(self, row) -> str | None:
        """Why this account can never receive this posting, or `None`."""
        # Title and company only, never the description.
        if self.excluded(row.get("title"), row.get("company")):
            return "excluded_keyword"

        if applicant_glut(source=row.get("source"), applicants=row.get("applicants"),
                          cap=self.applicant_cap):
            return "applicant_glut"

        # All three switched on accepts every mode, so the classifier — the costliest check here — is
        # skipped. `explicit=` is the tag parsed off the posting's own page, and it beats every heuristic.
        mode = None if self.workplaces >= set(WORKPLACES) else workfilter.classify(
            row.get("title") or "", row.get("location") or "", row.get("description"),
            row.get("is_remote"), explicit=row.get("workplace"))
        # Unknown is kept whatever is switched on: absence of a signal is not evidence of on-site.
        if mode and mode not in self.workplaces:
            return "workplace"

        # The stored code, detected once at describe time rather than per reader per pass.
        if not langfilter.is_allowed(row.get("language"), set(self.languages),
                                     source=row.get("source")):
            return "language"

        return None


def _fresh_enough(filters: UserFilters, *, now: datetime | None = None) -> dict:
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
    cutoff = {"$switch": {
        "branches": [{"case": {"$eq": ["$source", source]},
                      "then": reference - timedelta(hours=max(hours, filters.max_age_hours))}
                     for source, hours in sorted(floors.items())],
        "default": reference - timedelta(hours=max(DEFAULT_SOURCE_MIN_AGE_HOURS, filters.max_age_hours))}}
    # A midnight timestamp is a date, not a time: judge those, and postings with none, on when we found them.
    date_only = {"$or": [{"$eq": [{"$ifNull": ["$posted_at", None]}, None]},
                         {"$eq": ["$posted_at", {"$dateTrunc": {"date": "$posted_at", "unit": "day"}}]}]}
    clock = {"$cond": [date_only, "$first_seen_at", "$posted_at"]}
    return {"$expr": {"$gte": [clock, cutoff]}}


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
    from libs.tenant.repository import TenantRepository  # noqa: PLC0415 - avoids a cycle at import time

    windows = windows or platform_windows(session)
    # Kept next to the `None` that honours Unlimited: `test_unlimited_is_honoured_wherever_it_is
    # _offered` reads the few lines after a `resolve(...)` looking for it, and putting anything
    # between the two hides the check from the guard as well as from a reader.
    cap = limits.resolve(user, "applicant_cap")
    return UserFilters.from_config(
        TenantRepository(session).get_config(user.id),
        max_age_hours=windows.max_age_hours,
        applicant_cap=None if cap is None else int(cap),
        source_min_ages=windows.source_min_ages)


def candidate_conditions(filters: UserFilters, *, now: datetime | None = None) -> list[dict]:
    """The conditions every "is this posting a candidate for this account" question must ask, for `$and`."""
    return [
        # Nothing to apply for.
        {"closed_at": None},
        _fresh_enough(filters, now=now),
        # Their countries, or a posting that names no readable place.
        {} if not filters.countries
        else {"$or": [{"country": None}, {"country": {"$in": sorted(filters.countries)}}]},
        # A posting whose page refused to answer twice was abandoned, and its stored "description"
        # is its own title. Matching words against a sentence is worse than not matching.
        {"describe_failures": {"$lt": DESCRIBE_MAX_FAILURES}},
        # A job somebody wrote in themselves is theirs alone, reached through their own mark (Add a job).
        {"source": {"$ne": TYPED}},
    ]
