"""Source registry."""
from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Any, NamedTuple

from services.harvester.sources import ats, hiringcafe, http_apis, jobspy_source
from services.harvester.sources.base import JobLink


class Tier(StrEnum):
    """Who pays for a fetch, and therefore which loop may run it."""

    SHARED = "shared"
    PER_USER = "per_user"


class Source(NamedTuple):
    key: str
    adapter: Callable[..., list[JobLink]]
    tier: Tier
    needs_ats_repo: bool = False


# Ordered as in the reference, so a diff against it stays readable.
REGISTRY: dict[str, Source] = {
    s.key: s for s in (
        # JobSpy.
        Source("jobspy_linkedin", jobspy_source.gather_linkedin, Tier.PER_USER),
        Source("jobspy_indeed", jobspy_source.gather_indeed, Tier.SHARED),
        Source("jobspy_google", jobspy_source.gather_google, Tier.SHARED),

        Source("hiringcafe", hiringcafe.gather_hiringcafe, Tier.SHARED),

        # Direct company ATS boards: unproxied, and the only family reading harvested tokens and ETags.
        Source("greenhouse", ats.gather_greenhouse, Tier.SHARED, needs_ats_repo=True),
        Source("ashby", ats.gather_ashby, Tier.SHARED, needs_ats_repo=True),
        Source("lever", ats.gather_lever, Tier.SHARED, needs_ats_repo=True),
        Source("smartrecruiters", ats.gather_smartrecruiters, Tier.SHARED, needs_ats_repo=True),
        Source("recruitee", ats.gather_recruitee, Tier.SHARED, needs_ats_repo=True),
        Source("breezy", ats.gather_breezy, Tier.SHARED, needs_ats_repo=True),
        Source("workday", ats.gather_workday, Tier.SHARED, needs_ats_repo=True),
        Source("personio", ats.gather_personio, Tier.SHARED, needs_ats_repo=True),
        Source("workable", ats.gather_workable, Tier.SHARED, needs_ats_repo=True),

        # VC portfolio boards.

        # Board APIs — feed-shaped, so they need no query set at all (HARVESTER_PLAN.md §1).
        Source("nofluffjobs", http_apis.gather_nofluffjobs, Tier.SHARED),
        Source("nofluffjobs_hu", http_apis.gather_nofluffjobs, Tier.SHARED),
        Source("profession", http_apis.gather_profession, Tier.SHARED),
        Source("jooble", http_apis.gather_jooble, Tier.SHARED),
        Source("justjoinit", http_apis.gather_justjoinit, Tier.SHARED),
        Source("himalayas", http_apis.gather_himalayas, Tier.SHARED),
        Source("workingnomads", http_apis.gather_workingnomads, Tier.SHARED),
        Source("jobicy", http_apis.gather_jobicy, Tier.SHARED),
        Source("themuse", http_apis.gather_themuse, Tier.SHARED),
        Source("findwork", http_apis.gather_findwork, Tier.SHARED),
        Source("adzuna", http_apis.gather_adzuna, Tier.SHARED),
        Source("hn_hiring", http_apis.gather_hn_hiring, Tier.SHARED),
    )
}

SHARED_SOURCES = tuple(s.key for s in REGISTRY.values() if s.tier is Tier.SHARED)
PER_USER_SOURCES = tuple(s.key for s in REGISTRY.values() if s.tier is Tier.PER_USER)

# Outer fan-out cap, carried over from the 2026-07-24 CHANGELOG fix.
OUTER_CONCURRENCY = 8


def sources_for(tier: Tier) -> list[Source]:
    """Every source belonging to one tier."""
    return [s for s in REGISTRY.values() if s.tier is tier]


__all__ = [
    "OUTER_CONCURRENCY",
    "PER_USER_SOURCES",
    "REGISTRY",
    "SHARED_SOURCES",
    "JobLink",
    "Source",
    "Tier",
    "sources_for",
]
