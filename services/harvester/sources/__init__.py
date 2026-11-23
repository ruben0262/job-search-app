"""Source registry."""
from __future__ import annotations

from collections.abc import Callable
from typing import NamedTuple

from services.harvester.sources import ats, http_apis
from services.harvester.sources.base import JobLink


class Source(NamedTuple):
    key: str
    adapter: Callable[..., list[JobLink]]
    needs_ats_repo: bool = False


# Every board the shared run can read, each with its adapter: the ones on `libs.settings.HARVEST_SOURCES`, the
# one list that says which are read. LinkedIn is a pass of its own (`services.harvester.linkedin`), through
# the site proxy, and not a board here.
REGISTRY: dict[str, Source] = {
    s.key: s for s in (
        # Direct company ATS boards: unproxied, and the only family reading harvested tokens and ETags.
        Source("greenhouse", ats.gather_greenhouse, needs_ats_repo=True),
        Source("ashby", ats.gather_ashby, needs_ats_repo=True),
        Source("lever", ats.gather_lever, needs_ats_repo=True),
        Source("smartrecruiters", ats.gather_smartrecruiters, needs_ats_repo=True),
        Source("recruitee", ats.gather_recruitee, needs_ats_repo=True),
        Source("breezy", ats.gather_breezy, needs_ats_repo=True),
        Source("workday", ats.gather_workday, needs_ats_repo=True),
        Source("personio", ats.gather_personio, needs_ats_repo=True),
        Source("workable", ats.gather_workable, needs_ats_repo=True),
        Source("teamtailor", ats.gather_teamtailor, needs_ats_repo=True),

        # Board APIs — feed-shaped, so they need no query set at all (HARVESTER_PLAN.md §1).
        Source("nofluffjobs", http_apis.gather_nofluffjobs),
        Source("profession", http_apis.gather_profession),
        Source("justjoinit", http_apis.gather_justjoinit),
        Source("himalayas", http_apis.gather_himalayas),
        Source("workingnomads", http_apis.gather_workingnomads),
        Source("jobicy", http_apis.gather_jobicy),
        Source("findwork", http_apis.gather_findwork),
        Source("adzuna", http_apis.gather_adzuna),
        Source("arbeitnow", http_apis.gather_arbeitnow),
        Source("remoteok", http_apis.gather_remoteok),
        Source("platsbanken", http_apis.gather_platsbanken),
        Source("reed", http_apis.gather_reed),
        Source("teachingvacancies", http_apis.gather_teachingvacancies),
        Source("nav", http_apis.gather_nav),
        Source("feinaactiva", http_apis.gather_feinaactiva),
    )
}

# Outer fan-out cap, carried over from the 2026-07-24 CHANGELOG fix.
OUTER_CONCURRENCY = 8


__all__ = [
    "OUTER_CONCURRENCY",
    "REGISTRY",
    "JobLink",
    "Source",
]
