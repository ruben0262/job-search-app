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
from dataclasses import dataclass
from uuid import UUID

# Sources needing no API key and no credential.
#
# **This was seven for four days and it was the wrong seven.** The original set was chosen as a
# deliberate first notch — "sources are enabled incrementally rather than all at once… each has its
# own failure modes to learn" — and the ramp then stopped, because nothing measured that it had. The
# seven were all remote-work aggregators, which the reference instance does not use at all, and the
# result was a feed that scored: 1,293 fresh jobs over two days produced nine at or above the
# delivery cutoff, against LinkedIn's historical rate of one in ten.
#
# This is now the reference's own enabled set, minus the credentialled ones (which `KEY_GATED`
# handles) and plus the aggregators already running. The ATS boards are where employers post
# directly and are the ones worth having; they need `ats_tokens` seeded or they iterate an empty
# company list and return nothing, silently, because that is the adapter contract.
DEFAULT_ENABLED = (
    # Remote-work aggregators — the original seven, kept.
    "remotive", "himalayas", "remoteok", "arbeitnow",
    "weworkremotely", "workingnomads", "jobicy",
    # ATS boards: employers posting directly. Gated on `ats_tokens` having rows.
    "greenhouse", "ashby", "lever", "smartrecruiters",
    "recruitee", "breezy", "workday", "personio", "workable",
    # VC and aggregator boards the reference runs.
    "hiringcafe", "consider", "getro", "themuse",
    # European boards, which is where this platform's users actually are.
    "nofluffjobs", "nofluffjobs_hu", "profession", "dreamjobs", "justjoinit",
)

# Board APIs that iterate `cfg["queries"]` and do nothing without it. Getting this list wrong is
# silent: the adapter makes no request, logs nothing, and returns [] - which is its contract.
QUERY_TAKING_SOURCES = frozenset({
    "remotive", "himalayas", "arbeitnow", "hiringcafe", "nofluffjobs", "nofluffjobs_hu",
    "jooble", "consider", "getro",
})


@dataclass(frozen=True)
class QueryShape:
    """What one source can be asked for, and under which keys.

    **Every adapter reads a different key, and a query in the wrong shape is silent.** The board
    answers the unfiltered feed and reports success, so a mis-shaped query is indistinguishable from
    a broad one — which is how eight sources came to be fed `{"search": term}` when only two of them
    read `search`.

    `location_key` is the important one, and it is what makes the query set stop being redundant.
    A source that cannot filter by place gets **one query per keyword**, not one per
    keyword × location: sending it the same search four times under four location slugs is four
    identical HTTP requests, four times the budget, and one set of results.
    """

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


# Grounded in what each adapter actually sends, not in what would be tidy. Read alongside the
# `gather_*` functions in `sources/` — if one of those changes its parameter names, this is the
# other half of that change.
QUERY_SHAPES: dict[str, QueryShape] = {
    # `params = {"limit": 100, "search": ...}` — no geography at all.
    "remotive": QueryShape("search"),
    # `params = {"search": ...}` plus a `remote` flag it filters on locally.
    "arbeitnow": QueryShape("search", remote_key="remote"),
    # `params["query"]`, and seniority. No location.
    "himalayas": QueryShape("query"),
    # The only free-tier source with a real location model: named regions plus workplace types.
    "hiringcafe": QueryShape("search_term", location_key="location", remote_key="is_remote"),
    # `criteriaSearch.requirement`. Geography is `criteriaSearch.city`, set per source from `cities`
    # rather than per query — which is why a region-level stem is meaningless here and the axis is
    # collapsed rather than sent.
    "nofluffjobs": QueryShape("keyword"),
    "nofluffjobs_hu": QueryShape("keyword"),
    # POST {"keywords", "location"} — takes both.
    "jooble": QueryShape("keywords", location_key="location"),
    # A list of plain strings used as `searchText`.
    "consider": QueryShape(None, bare=True),
    "getro": QueryShape(None, bare=True),
}

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


def _from_sections(config: Mapping, field: str) -> list[str]:
    """One repeated field, from wherever a config actually keeps it.

    **Both shapes, and the sections are the real one.** `SearchSection.keywords` is where the schema
    puts a user's terms and where every stored config on the platform has them; the flat form is
    what older callers and fixtures pass. Reading only the flat one is the bug this function exists
    to stop repeating — it already happened to `posted_within_hours`, four lines below, and then
    happened again to the two fields beside it: `union_keywords` and `union_locations` both read the
    top level, both returned empty against every real config, and so **every board on the free tier
    fetched its unfiltered recent feed** while the platform behaved as though nobody had signed up.
    """
    stored = config or {}
    values = list(stored.get(field) or [])
    for section in stored.get("sections") or []:
        values += list((section or {}).get(field) or [])
    return values


def union_keywords(configs: Mapping[UUID, Mapping]) -> list[str]:
    """Every active user's keywords, deduplicated, lowercased.

    **The de-personalisation.** A feed-shaped source is pre-filtered on this rather than on one
    person's terms, so a job any user asked for survives the filter and reaches the shared corpus.

    Order is sorted rather than insertion-based so a cycle is reproducible: the same active users
    produce the same filter, regardless of the order the database returned them.
    """
    terms = {
        str(term).strip().lower()
        for config in configs.values()
        for term in _from_sections(config, "keywords")
        if str(term).strip()
    }
    return sorted(terms)


def union_locations(configs: Mapping[UUID, Mapping]) -> list[str]:
    """Every active user's locations. Same reasoning as `union_keywords`."""
    locations = {
        str(loc).strip().lower()
        for config in configs.values()
        for loc in _from_sections(config, "locations")
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
    #
    # **Read from the sections, which is where the field lives.** It was read from the top level of
    # the config, where nothing has ever put it, so every generator yielded 0 and every harvest ran
    # at `DEFAULT_MAX_AGE_HOURS` no matter what anybody asked for. A user who set six hours got
    # seventy-two, silently, and the only visible symptom was a feed full of old postings.
    # Both shapes. `SearchSection.posted_within_hours` is where the schema puts it and where every
    # stored config actually has it; the flat form is what older callers and fixtures pass. Reading
    # only the flat one is what made this always return the default.
    windows = []
    for stored in configs.values():
        config = stored or {}
        windows.append(int(config.get("posted_within_hours") or 0))
        windows += [int(section.get("posted_within_hours") or 0)
                    for section in config.get("sections") or []]
    max_age = max((window for window in windows if window > 0),
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
            cfg["queries"] = _queries_for(source, keywords)
        out[source] = cfg
    return out


def _queries_for(source: str, keywords: list[str]) -> list[dict | str]:
    """Query objects for board APIs that iterate `cfg["queries"]`.

    **Found on the first real cycle.** `remotive`, `himalayas` and `arbeitnow` loop over
    `cfg.get("queries", [])` and return `[]` when it is absent — so with no queries configured they
    made no HTTP request at all, logged nothing, and reported success. An adapter returning `[]` is
    the registry's contract, which is exactly why the silence was total.

    The empty-search fallback is not a placeholder: with no users yet, an empty term fetches the
    board's unfiltered recent feed, which is precisely "harvest broadly, match per user"
    (`HARVESTER_PLAN.md` §1) and is what gives a brand-new user a non-empty first feed.

    **Rendered per source.** This emitted `{"search": term}` for all nine, and only Remotive and
    Arbeitnow read `search` — Himalayas wants `query`, NoFluffJobs `keyword`, HiringCafe
    `search_term`, Jooble `keywords`, and Consider and Getro want a bare string. The other seven
    therefore received a dict they ignored and fetched their unfiltered feed, silently, which is
    what a mis-shaped query always looks like from outside.
    """
    shape = QUERY_SHAPES[source]
    return [shape.render(term=term, location="", is_remote=False)
            for term in keywords] or [shape.render(term="", location="", is_remote=False)]
