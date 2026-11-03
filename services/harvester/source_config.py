"""Platform source configuration, de-personalised."""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from libs.contracts.config import migrate_config
from libs.settings import QUERY_TAKING_SOURCES

# Sources needing no API key and no credential.
DEFAULT_ENABLED = (
    # Remote-work aggregators that filter on what we ask for.
    "himalayas", "workingnomads", "jobicy",
    # ATS boards: employers posting directly. Gated on `ats_tokens` having rows.
    "greenhouse", "ashby", "lever", "smartrecruiters",
    "recruitee", "breezy", "workday", "personio", "workable",
    # VC and aggregator boards the reference runs.
    "hiringcafe", "themuse",
    # European boards, which is where this platform's users actually are.
    "nofluffjobs", "profession", "justjoinit",
)

# Removed 2026-08-04, with the measurement that removed them.
DEFERRED_SOURCES = ("arbeitnow", "remotive", "remoteok", "dreamjobs",
                    "consider", "getro", "nofluffjobs_hu", "weworkremotely")


# Adapters that keep only the jobs somebody's words name, reading `cfg["keywords"]` themselves. Every
# other feed keeps all it reads and the words are matched here, so a word added tomorrow finds today's
# jobs, and a feed costs the same however many words there are (2026-11-03). Just Join IT stays filtered
# until the database has room: unfiltered it is ~2,650 jobs a day, ~330 MB over the 30 days kept,
# against the 512 MB the Atlas plan holds (measured 2026-11-03). Himalayas the same: read as a list now,
# ~2,000 jobs a day, not a search per word; and No Fluff Jobs, its whole list read every hour.
KEYWORD_FILTER_SOURCES = frozenset({"justjoinit", "himalayas", "nofluffjobs"})


# Sources whose rows are dropped client-side by `ats.JobFilter` against the union's terms.
PREFILTER_SOURCES: frozenset[str] = frozenset()


# The config key each adapter reads for "how many results".
RESULT_KEYS: dict[str, str] = {
    "justjoinit": "per_page",
    "jobicy": "count",
    "hiringcafe": "results_wanted",
}

# The ATS family polls company boards rather than issuing queries, so its ceiling is a company count.
ATS_SOURCES: frozenset[str] = frozenset({
    "greenhouse", "ashby", "lever", "smartrecruiters", "recruitee",
    "breezy", "workday", "personio", "workable",
})


@dataclass(frozen=True)
class QueryShape:
    """What one source can be asked for, and under which keys."""

    # Where the keyword goes. `None` with `bare` means the query *is* the term, as a plain string.
    term_key: str | None
    # Where the location goes, or `None` when the source has nowhere to put one.
    location_key: str | None = None
    remote_key: str | None = None
    # Consider and Getro iterate a list of strings, not dicts, and use each one as `searchText`.
    bare: bool = False

    def render(self, *, term: str, location: str, is_remote: bool) -> dict | str:
        """One planned query, in the shape this adapter reads."""
        if self.bare:
            return term
        query: dict = {self.term_key: term} if self.term_key else {}
        if self.location_key and location:
            query[self.location_key] = location
        if self.remote_key and is_remote:
            query[self.remote_key] = True
        return query


# Grounded in what each adapter actually sends, not in what would be tidy.
QUERY_SHAPES: dict[str, QueryShape] = {
    # The one free-tier source with a real location model: named regions plus workplace types.
    "hiringcafe": QueryShape("search_term", location_key="location", remote_key="is_remote"),
    # POST {"keywords", "location"} — takes both. Key-gated, so unmeasured.
    "jooble": QueryShape("keywords", location_key="location"),
}

# How far back to ask a source, when no user has asked for a window and the operator has set none.
DEFAULT_HARVEST_WINDOW_HOURS = 72

# Sources gated on a credential.
KEY_GATED = {
    "findwork": "FINDWORK_API_KEY",
    "adzuna": "ADZUNA_APP_ID",
}

# `jooble` is not here on purpose, and holding the key is not enough to bring it back. This map is
# what actually enables a source — it appends to `active` whenever the credential is present,
# independent of `HARVEST_SOURCES` — so removing it from that tuple alone would have left it
# running. Probed 2026-08-10 from the VPS with a valid key: every host returns an HTML 403 block
# page. The block is on the address, not the credential.

# Endpoints and rate limits: genuinely platform-level, lifted from the reference's config.yaml.
PLATFORM_DEFAULTS: dict[str, dict] = {
    "himalayas": {"endpoint": "https://himalayas.app/jobs/api"},
    # RemoteOK rate-limits a burst from one IP; the delay is from the reference and is not a guess.
    "workingnomads": {"endpoint": "https://www.workingnomads.com/api/exposed_jobs/"},
    "jobicy": {"count": 50},
    "hiringcafe": {"posted_within_days": 3},
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


def union_keywords(configs: Mapping[UUID, Mapping]) -> list[str]:
    """Every active user's keywords, deduplicated, lowercased."""
    # Lowercased and nothing else.
    terms = {
        str(term).strip().lower()
        for config in configs.values()
        for term in _from_sections(config, "keywords")
        if str(term).strip()
    }
    return sorted(terms)


def union_locations(configs: Mapping[UUID, Mapping]) -> list[str]:
    """Every active user's locations. Same reasoning as `union_keywords`. Read as the current places
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
    keywords = union_keywords(configs)
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
        # Two different consumers of the same word, and only one of them is safe.
        if keywords and (source in KEYWORD_FILTER_SOURCES or source in PREFILTER_SOURCES):
            cfg["keywords"] = keywords
        if locations and source in PREFILTER_SOURCES:
            cfg["location_keywords"] = locations
        if source in QUERY_TAKING_SOURCES:
            cfg["queries"] = _queries_for(source, keywords)
        if source == "adzuna":
            _shape_adzuna(cfg, keywords=keywords, locations=locations,
                          # `queries_per_cycle`, not `results_per_query`: this is how many calls
                          # the source may make, which is what the free tier meters. Wiring it to
                          # the results knob meant the panel's queries control did nothing here.
                          max_queries=(queries or {}).get("adzuna"))
        out[source] = cfg
    return out


def _apply_window(cfg: dict, window: int) -> None:
    """One harvest window, under every key an adapter might read it as."""
    # Both, because the two tiers read different ones.
    cfg["max_age_hours"] = window
    cfg["hours_old"] = window
    # `ats.JobFilter` prefers `posted_within_hours`, and PLATFORM_DEFAULTS silently outranked this.
    if "posted_within_hours" in cfg:
        cfg["posted_within_hours"] = window
    # And HiringCafe counts in days.
    if "posted_within_days" in cfg:
        cfg["posted_within_days"] = max(1, -(-window // 24))


def _shape_adzuna(cfg: dict, *, keywords: list[str], locations: list[str],
                  max_queries: int | None) -> None:
    """Adzuna iterates (country x keyword) itself rather than a query set, and was given neither.

    So it fell back to its own hardcoded `["python developer", "backend engineer"]` in `gb` with
    `where="remote"` — a source added to widen the corpus beyond software would have searched only
    for remote software. The adapter's defaults are gone; this is where it gets its demand.
    """
    cfg["keywords"] = keywords
    cfg["countries"] = adzuna_countries(locations)
    if max_queries:
        cfg["max_queries"] = int(max_queries)


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


def _queries_for(source: str, keywords: list[str]) -> list[dict | str]:
    """Query objects for board APIs that iterate `cfg["queries"]`."""
    shape = QUERY_SHAPES[source]
    return [shape.render(term=term, location="", is_remote=False)
            for term in keywords] or [shape.render(term="", location="", is_remote=False)]
