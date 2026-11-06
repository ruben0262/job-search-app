"""Platform source configuration, de-personalised."""
from __future__ import annotations

import os
from collections.abc import Mapping
from uuid import UUID

from libs.contracts.config import migrate_config

# Sources needing no API key and no credential. Each is read whole, every job in its window kept, and
# people's words are matched on the stored jobs (owner, 2026-11-04).
DEFAULT_ENABLED = (
    # Remote-work aggregators.
    "himalayas", "workingnomads", "jobicy",
    # ATS boards: employers posting directly. Gated on `ats_tokens` having rows.
    "greenhouse", "ashby", "lever", "smartrecruiters",
    "recruitee", "breezy", "workday", "personio", "workable",
    # Aggregators.
    "themuse",
    # European boards, which is where this platform's users actually are.
    "nofluffjobs", "profession", "justjoinit",
)

# Removed 2026-08-04, with the measurement that removed them.
DEFERRED_SOURCES = ("arbeitnow", "remotive", "remoteok", "dreamjobs",
                    "consider", "getro", "nofluffjobs_hu", "weworkremotely")


# The config key each adapter reads for "how many results".
RESULT_KEYS: dict[str, str] = {
    "justjoinit": "per_page",
    "jobicy": "count",
}

# The ATS family polls company boards rather than issuing queries, so its ceiling is a company count.
ATS_SOURCES: frozenset[str] = frozenset({
    "greenhouse", "ashby", "lever", "smartrecruiters", "recruitee",
    "breezy", "workday", "personio", "workable",
})


# How far back to ask a source, when no user has asked for a window and the operator has set none.
DEFAULT_HARVEST_WINDOW_HOURS = 72

# Sources gated on a credential.
KEY_GATED = {
    "findwork": "FINDWORK_API_KEY",
    "adzuna": "ADZUNA_APP_ID",
}

# Endpoints and rate limits: genuinely platform-level, lifted from the reference's config.yaml.
PLATFORM_DEFAULTS: dict[str, dict] = {
    "himalayas": {"endpoint": "https://himalayas.app/jobs/api"},
    # RemoteOK rate-limits a burst from one IP; the delay is from the reference and is not a guess.
    "workingnomads": {"endpoint": "https://www.workingnomads.com/api/exposed_jobs/"},
    "jobicy": {"count": 50},
    # The direct-ATS family.
    **{
        ats: {"max_companies": 400, "posted_within_hours": 72, "use_harvested": True,
              "conditional": True}
        for ats in ("greenhouse", "ashby", "lever", "smartrecruiters", "breezy",
                    "workday", "personio")
    },
    # Recruitee answers a burst from one IP with 429s — lower concurrency, from the reference.
    "recruitee": {"max_companies": 400, "posted_within_hours": 72, "use_harvested": True,
                  "conditional": True, "concurrency": 8},
    # Workable more so: 16 at once got 490 refusals of 555, and then even one at a time was refused for
    # hours (2026-11-02). The harvest asked 30 at once.
    "workable": {"max_companies": 400, "posted_within_hours": 72, "use_harvested": True,
                 "conditional": True, "concurrency": 2},
}


def _from_sections(config: Mapping, field: str) -> list[str]:
    """One repeated field, from wherever a config actually keeps it."""
    stored = config or {}
    values = list(stored.get(field) or [])
    for section in stored.get("sections") or []:
        values += list((section or {}).get(field) or [])
    return values


def union_locations(configs: Mapping[UUID, Mapping]) -> list[str]:
    """Every active user's locations, deduplicated, lowercased. Read as the current places
    (`migrate_config`): a city picked before My search picked countries is its country."""
    locations = {
        str(loc).strip().lower()
        for config in configs.values()
        for loc in _from_sections(migrate_config(dict(config or {})), "locations")
        if str(loc).strip()
    }
    return sorted(locations)


def build_source_configs(
    configs: Mapping[UUID, Mapping],
    *,
    enabled: tuple[str, ...] | None = None,
    env: Mapping[str, str] | None = None,
    freshness: Mapping[str, int] | None = None,
    pages: Mapping[str, int] | None = None,
    results: Mapping[str, int] | None = None,
    queries: Mapping[str, int] | None = None,
    ats_companies: int | None = None,
) -> dict[str, dict]:
    """Platform defaults plus the union-derived filters, for every enabled source."""
    env = env if env is not None else os.environ
    locations = union_locations(configs)

    active = list(enabled if enabled is not None else DEFAULT_ENABLED)
    for source, key in KEY_GATED.items():
        # Enabled only with a credential: a source that 401s every cycle drowns real failures.
        if env.get(key):
            active.append(source)

    # The widest window any active user asked for.
    windows = []
    for stored in configs.values():
        config = stored or {}
        windows.append(int(config.get("posted_within_hours") or 0))
        windows += [int(section.get("posted_within_hours") or 0)
                    for section in config.get("sections") or []]
    max_age = max((window for window in windows if window > 0),
                  default=0) or DEFAULT_HARVEST_WINDOW_HOURS

    out: dict[str, dict] = {}
    for source in active:
        cfg = dict(PLATFORM_DEFAULTS.get(source, {}))
        cfg["enabled"] = True
        _apply_window(cfg, int((freshness or {}).get(source) or 0) or max_age)
        # How deep one query reaches, under whichever key this adapter reads.
        depth = (results or {}).get(source)
        if depth and source in RESULT_KEYS:
            cfg[RESULT_KEYS[source]] = int(depth)
        # How many company boards one ATS cycle polls.
        if ats_companies and source in ATS_SOURCES:
            cfg["max_companies"] = int(ats_companies)
        # How deep a paginated source walks.
        page_depth = (pages or {}).get(source)
        if page_depth:
            cfg["max_pages"] = int(page_depth)
        if source == "adzuna":
            cfg["countries"] = adzuna_countries(locations)
            # `queries_per_cycle`, not `results_per_query`: this is how many calls the source may make,
            # which is what the free tier meters.
            if max_queries := (queries or {}).get("adzuna"):
                cfg["max_queries"] = int(max_queries)
        out[source] = cfg
    return out


def _apply_window(cfg: dict, window: int) -> None:
    """One harvest window, under every key an adapter might read it as."""
    cfg["max_age_hours"] = window
    # `ats.JobFilter` prefers `posted_within_hours`, and PLATFORM_DEFAULTS silently outranked this.
    if "posted_within_hours" in cfg:
        cfg["posted_within_hours"] = window


def adzuna_countries(locations: list[str]) -> list[str]:
    """The Adzuna country codes the active users' locations actually admit.

    `libs.corpus.geo` is the one place that turns a place into countries, and it is reused here
    rather than restated: a second mapping is the defect this project ships most often. Adzuna
    serves a subset, so the result is intersected with what the vendor answers for.
    """
    from libs.corpus.geo import wanted_countries  # noqa: PLC0415 - one caller, kept local
    from services.harvester.sources.http_apis import ADZUNA_COUNTRIES  # noqa: PLC0415

    wanted = wanted_countries(locations) or frozenset()
    return sorted({code.lower() for code in wanted} & ADZUNA_COUNTRIES)
