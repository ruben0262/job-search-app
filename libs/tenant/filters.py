"""What one account will accept, as conditions the database applies: one question, asked in one place."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from libs.contracts.config import migrate_config
from libs.corpus.freshness import PlatformWindows, platform_windows, showable
from libs.corpus.geo import wanted_countries
from libs.corpus.repository import TYPED
from libs.db.models.corpus import WORKPLACES
from libs.limits import limit_default

# The catalogue's ceiling, for a caller with no user to resolve against — a test, or a direct construction.
CATALOGUE_APPLICANT_CAP = limit_default("applicant_cap")


def _workplaces(sections: list[dict]) -> frozenset[str]:
    """Every way of working switched on in any section, so no section narrows another one's feed. A
    section with none switched on narrows nothing. Where a posting is, is the country filter's job."""
    switched = [set(section.get("workplaces") or WORKPLACES) for section in sections]
    return frozenset().union(*switched) if switched else frozenset(WORKPLACES)


@dataclass(frozen=True)
class UserFilters:
    """What one account asked for, read from their config once: `candidate_conditions` makes it a query."""

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


# `filters_for` reads the config itself unless it is given one: None is a config, an account that has none.
_UNREAD: dict = {}


def filters_for(session, user, *, windows: PlatformWindows | None = None,
                config: dict | None = _UNREAD) -> UserFilters:
    """This account's pre-score conditions, with the operator's numbers already resolved.

    One construction site for the three lanes that ask the same question, so the ceiling an operator
    sets and the window they set arrive together rather than one of them being forgotten.

    `windows` is the platform half, for a caller looping over accounts: pass it and the two settings
    are read once rather than once per account. Omit it and it is read here, so a caller with one
    account stays a one-liner. `config` likewise: their stored config, for a caller that read everybody's
    at once.
    """
    from libs import limits  # noqa: PLC0415 - avoids a cycle at import time
    from libs.tenant.repository import TenantRepository  # noqa: PLC0415 - avoids a cycle at import time

    windows = windows or platform_windows(session)
    # Kept next to the `None` that honours Unlimited: `test_unlimited_is_honoured_wherever_it_is
    # _offered` reads the few lines after a `resolve(...)` looking for it, and putting anything
    # between the two hides the check from the guard as well as from a reader.
    cap = limits.resolve(user, "applicant_cap")
    return UserFilters.from_config(
        TenantRepository(session).get_config(user.id) if config is _UNREAD else config,
        max_age_hours=windows.max_age_hours,
        applicant_cap=None if cap is None else int(cap),
        source_min_ages=windows.source_min_ages)


def candidate_conditions(filters: UserFilters, *, now: datetime | None = None) -> list[dict]:
    """The conditions every "is this posting a candidate for this account" question must ask, for `$and`."""
    return [
        # Nothing to apply for.
        {"closed_at": None},
        showable(PlatformWindows(filters.max_age_hours, filters.source_min_ages), now=now),
        # Their countries, or a posting that names no readable place (`None` is both a null and no field).
        # Asked as one `$in`, which the words index reads country by country (`open_terms_newest`).
        {} if not filters.countries else {"country": {"$in": [None, *sorted(filters.countries)]}},
        # Its whole text read (`CorpusRepository.set_description`). Shown on its title alone, a job left
        # the page when its text came in: another language, a way of working not wanted, more applicants
        # than the cap, a salary under the minimum (owner, 2026-11-06). A page that refused twice was
        # given up on and never gets here.
        {"described_at": {"$ne": None}},
        # A job somebody wrote in themselves is theirs alone, reached through their own mark (Add a job).
        {"source": {"$ne": TYPED}},
        *_their_own(filters),
    ]


def _their_own(filters: UserFilters) -> list[dict]:
    """What this person said they do not want, each as a condition; one that is off is no condition.

    These were Python, run over every candidate after it was read: of the 11,218 postings a month held for
    one account's words, 6,418 were read, sent and decoded only to be dropped here (2026-11-15). Asked of
    the database they are never sent, and there is one place that decides, not a query and a function
    after it. Unknown is always kept: no language read, no way of working stated, no applicant count.
    """
    asked = []
    if red := "|".join(re.escape(term) for term in filters.exclude if term):
        # Whole words: "sr" is a reasonable thing to type and a disastrous substring ("Israel"). The title
        # and the company only, never the description, which mentions a word without being about it.
        named = {"$regex": rf"(*UCP)(?<!\w)(?:{red})(?!\w)", "$options": "i"}
        asked.append({"$nor": [{"title": named}, {"company": named}]})
    if filters.applicant_cap > 0:
        # Over-subscribed. Only LinkedIn reports a count worth trusting.
        asked.append({"$nor": [{"source": "linkedin", "applicants": {"$gte": filters.applicant_cap}}]})
    if not filters.workplaces >= set(WORKPLACES):
        # The way of working read when the posting's text was stored (`CorpusRepository.set_description`),
        # from the board's own tag, its title, its place and its text.
        asked.append({"workplace": {"$in": [None, *sorted(filters.workplaces)]}})
    if filters.languages:
        # The stored code, detected once when the text was stored, never per reader.
        asked.append({"language": {"$in": [None, *sorted(filters.languages)]}})
    return asked
