"""Source registry. Each adapter yields `JobLink`s and **never raises**.

That last property is the reference's Phase-1 error-handling rule and it survives the port: a source
that fails logs and returns `[]`, so one dead board cannot sink a harvest cycle. Sources break
upstream constantly — a changed JSON path, an expired certificate, a rate limit — and a cycle that
aborts on the first one delivers nothing to anybody.

Two things this registry knows that the reference's did not:

1. **Which tier a source belongs to.** The shared tier is fetched once for everyone; the per-user
   tier runs on one user's proxy credential and only for them (D3). Tagging it here means the
   distinction is data rather than something each caller re-derives — and getting it wrong is how a
   proxied source silently ends up in the shared loop, spending one user's bandwidth for all.
2. **Which adapters need the ATS repository.** Only the direct-ATS family reads tokens and ETags, so
   only it takes a repository argument.

Tier-4 (`claude_supplementary`) is deliberately absent, as in the reference: it runs inside the
scoring prompt, not as a scraped source.
"""
from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Any, NamedTuple

from services.harvester.sources import ats, hiringcafe, http_apis, jobspy_source
from services.harvester.sources.base import JobLink


class Tier(StrEnum):
    """Who pays for a fetch, and therefore which loop may run it.

    `SHARED` costs nobody anything and is harvested once for everyone. `PER_USER` needs a proxy
    credential, runs on its owner's schedule, and its rows are gated behind that credential's health
    (`HARVESTER_PLAN.md` §5).
    """

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
        # JobSpy. LinkedIn is the ONLY per-user source: it is the only one that needs a residential
        # proxy, and after the 2026-07-26 routing change it is the only thing proxied at all.
        Source("jobspy_linkedin", jobspy_source.gather_linkedin, Tier.PER_USER),
        Source("jobspy_indeed", jobspy_source.gather_indeed, Tier.SHARED),
        Source("jobspy_google", jobspy_source.gather_google, Tier.SHARED),

        Source("hiringcafe", hiringcafe.gather_hiringcafe, Tier.SHARED),

        # Direct company ATS boards — roughly two thirds of all sources, all unproxied, and the
        # only family that reads harvested tokens and conditional-poll ETags.
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

# Outer fan-out cap, carried over from the 2026-07-24 CHANGELOG fix. JobSpy sources open their OWN
# nested pools, so an unbounded `max_workers=len(enabled)` (~30) multiplied against those spawned
# ~180 threads per scrape. Eight network-bound sources at a time saturates the link without the
# runaway CPU spike. **Do not raise this.**
OUTER_CONCURRENCY = 8


def sources_for(tier: Tier) -> list[Source]:
    """Every source belonging to one tier.

    The shared loop asks for `Tier.SHARED` and structurally cannot be handed the proxied one, which
    is the same guarantee `make_shared_client()` gives at the HTTP layer — stated twice because it
    is the property D3 rests on.
    """
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
