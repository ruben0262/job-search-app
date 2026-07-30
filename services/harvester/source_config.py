"""Platform source configuration, de-personalised.

The reference's `config.yaml` carries per-source keyword lists that are **Ruben's** — they act as a
pre-filter so a feed-shaped board does not return the whole internet. Multi-tenant cannot use one
person's terms: the corpus is shared, so filtering it to one user's keywords would silently deny
every other user the jobs they asked for.

**So the split is: endpoints and rate limits are platform config; every keyword is derived from the
union of active users' configs** (`HARVESTER_PLAN.md` §4, roadmap step 4).

The principle from §1 governs the rest: **harvest broadly, match per user.** The pre-filter exists
only to stop a feed source returning obviously irrelevant volume; it is not a substitute for
per-user scoring, and making it tight would defeat the shared corpus's cold-start advantage — a new
user's first feed comes from jobs harvested before they signed up.

**Sources are enabled incrementally, not all at once.** Thirty-three sources on a first real run is
precisely what the fan-out budget exists to prevent, and each one has its own failure modes to
learn. `DEFAULT_ENABLED` is the no-auth set that needs nothing but a network; the rest are switched
on deliberately, with their cycle cost measured (`HARVESTER_PLAN.md` §3).
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from uuid import UUID

# Sources needing no API key and no credential. The starting set: each is a public JSON or RSS
# endpoint, and none of them costs anybody anything.
DEFAULT_ENABLED = (
    "remotive", "himalayas", "remoteok", "arbeitnow",
    "weworkremotely", "workingnomads", "jobicy",
)

# Board APIs that iterate `cfg["queries"]` and do nothing without it. Getting this list wrong is
# silent: the adapter makes no request, logs nothing, and returns [] - which is its contract.
QUERY_TAKING_SOURCES = frozenset({
    "remotive", "himalayas", "arbeitnow", "hiringcafe", "nofluffjobs", "nofluffjobs_hu",
    "jooble", "consider", "getro",
})

# The recency window when no user has asked for one. Matches the config schema's default rather
# than the adapters' internal 24h, which was fetching a much narrower slice than intended.
DEFAULT_MAX_AGE_HOURS = 72

# Sources gated on a credential. Enabled only when their key is present, because a source that
#401s every cycle is noise in the log and a failure count that means nothing.
KEY_GATED = {
    "jooble": "JOOBLE_API_KEY",
    "findwork": "FINDWORK_API_KEY",
    "adzuna": "ADZUNA_APP_ID",
}

# Endpoints and rate limits: genuinely platform-level, lifted from the reference's config.yaml.
# Nothing personal here — no keywords, no locations, no seniority.
PLATFORM_DEFAULTS: dict[str, dict] = {
    "remotive": {"endpoint": "https://remotive.com/api/remote-jobs"},
    "himalayas": {"endpoint": "https://himalayas.app/jobs/api"},
    # RemoteOK rate-limits a burst from one IP; the delay is from the reference and is not a guess.
    "remoteok": {"endpoint": "https://remoteok.com/api", "request_delay_s": 2},
    "arbeitnow": {"endpoint": "https://www.arbeitnow.com/api/job-board-api"},
    "weworkremotely": {"feeds": ["https://weworkremotely.com/categories/remote-programming-jobs.rss"]},
    "workingnomads": {"endpoint": "https://www.workingnomads.com/api/exposed_jobs/"},
    "jobicy": {"count": 50},
    "hiringcafe": {"posted_within_days": 3},
    # The direct-ATS family. `max_companies` bounds a cycle: the token list grows on its own from
    # harvested apply-URLs, and unbounded it would eventually poll thousands of boards per cycle.
    **{
        ats: {"max_companies": 400, "posted_within_hours": 72, "use_harvested": True,
              "conditional": True}
        for ats in ("greenhouse", "ashby", "lever", "smartrecruiters", "breezy",
                    "workday", "personio", "workable")
    },
    # Recruitee answers a burst from one IP with 429s — lower concurrency, from the reference.
    "recruitee": {"max_companies": 400, "posted_within_hours": 72, "use_harvested": True,
                  "conditional": True, "concurrency": 8},
}


def union_keywords(configs: Mapping[UUID, Mapping]) -> list[str]:
    """Every active user's keywords, deduplicated, lowercased.

    **The de-personalisation.** A feed-shaped source is pre-filtered on this rather than on one
    person's terms, so a job any user asked for survives the filter and reaches the shared corpus.

    Order is sorted rather than insertion-based so a cycle is reproducible: the same active users
    produce the same filter, regardless of the order the database returned them.
    """
    terms = {
        term.strip().lower()
        for config in configs.values()
        for term in (config.get("keywords") or [])
        if str(term).strip()
    }
    return sorted(terms)


def union_locations(configs: Mapping[UUID, Mapping]) -> list[str]:
    """Every active user's locations. Same reasoning as `union_keywords`."""
    locations = {
        str(loc).strip().lower()
        for config in configs.values()
        for loc in (config.get("locations") or [])
        if str(loc).strip()
    }
    return sorted(locations)


def build_source_configs(
    configs: Mapping[UUID, Mapping],
    *,
    enabled: tuple[str, ...] | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, dict]:
    """Platform defaults plus the union-derived filters, for every enabled source.

    A source with **no users' keywords at all** is still enabled and simply unfiltered — that is the
    cold-start case, where the platform harvests broadly before anybody has configured anything, and
    it is what makes a new user's first feed non-empty.
    """
    env = env if env is not None else os.environ
    keywords = union_keywords(configs)
    locations = union_locations(configs)

    active = list(enabled if enabled is not None else DEFAULT_ENABLED)
    for source, key in KEY_GATED.items():
        # Enabled only with a credential: a source that 401s every cycle produces a failure count
        # that means nothing and drowns real failures.
        if env.get(key):
            active.append(source)

    # The widest window any active user asked for. Everyone else filters down locally, which is the
    # same "fetch once at the coarsest demand" rule the query-set fold applies to the JobSpy tier.
    max_age = max(
        (int(c.get("posted_within_hours") or 0) for c in configs.values()),
        default=0) or DEFAULT_MAX_AGE_HOURS

    out: dict[str, dict] = {}
    for source in active:
        cfg = dict(PLATFORM_DEFAULTS.get(source, {}))
        cfg["enabled"] = True
        # **Both keys, because the two tiers read different ones.** The ATS and REST adapters
        # filter on `max_age_hours`; the JobSpy tier (LinkedIn, Indeed, Google) passes `hours_old`
        # straight to the scraper and ignores anything else. Setting only the first is what put
        # month-old, already-closed LinkedIn postings into live feeds: the age filter was real for
        # two thirds of the sources and absent for the ones that produce most of the volume.
        #
        # The reference's watcher does the same thing explicitly and says why: "the JobSpy sources
        # use `hours_old` instead ... so force `hours_old` to the same window, else we'd stream a
        # day of jobs, not the freshest slice."
        cfg["max_age_hours"] = max_age
        cfg["hours_old"] = max_age
        if keywords:
            cfg["keywords"] = keywords
        if locations:
            cfg["location_keywords"] = locations
        if source in QUERY_TAKING_SOURCES:
            cfg["queries"] = _queries_for(keywords)
        out[source] = cfg
    return out


def _queries_for(keywords: list[str]) -> list[dict]:
    """Query objects for board APIs that iterate `cfg["queries"]`.

    **Found on the first real cycle.** `remotive`, `himalayas` and `arbeitnow` loop over
    `cfg.get("queries", [])` and return `[]` when it is absent — so with no queries configured they
    made no HTTP request at all, logged nothing, and reported success. An adapter returning `[]` is
    the registry's contract, which is exactly why the silence was total.

    The empty-search fallback is not a placeholder: with no users yet, `{"search": ""}` fetches the
    board's unfiltered recent feed, which is precisely "harvest broadly, match per user"
    (`HARVESTER_PLAN.md` §1) and is what gives a brand-new user a non-empty first feed.
    """
    return [{"search": term} for term in keywords] or [{"search": ""}]
