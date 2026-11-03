"""Hiring Cafe (hiring.cafe) — server-rendered job search, NO auth required.

Why/how it works (reverse-engineered — see docs/CHANGELOG.md): hiring.cafe renders its results
server-side, so the jobs are embedded in the page. We GET

    https://hiring.cafe/?searchState=<url-encoded JSON>&page=<n>

and read them from  __NEXT_DATA__ -> props.pageProps.ssrHits  (40/page, page 0-indexed), with
ssrIsLastPage / ssrTotalCount for pagination. No API key, no login (the /api/search-jobs endpoint
is auth-guarded; this SSR path is the public one the site itself renders).

The `searchState` carries the filters:
  - locations: a geocoded location object (continent/country/city) whose `workplace_types`
    (["Remote"] or [] = all) is how we express remote-vs-onsite.
  - searchQuery: the keyword.
  - dateFetchedPastNDays: recency (hiring.cafe only offers day granularity).

Each hit's `apply_url` is a DIRECT apply link (Greenhouse/Lever/join.com/…), so it's both the job
url and where to apply. This source is EXEMPT from the language filter (see lib/langfilter.py) —
non-en/hu descriptions are acceptable here per Ruben.

--- ported from the reference instance, 2026-07-27 ---

Lifted from `job-search/pipeline/sources/hiringcafe.py` by
`retired/services/harvester/sources/port_from_reference.py`. Parsing, pagination and per-source quirks are
unchanged on purpose — they are the tuned part, and a faithful copy stays diffable against the
original.

The one systematic change: this module no longer constructs `httpx.Client` itself. Every client
comes from `services.harvester.fetch`, which is what keeps "whose credential paid for this fetch"
answerable, and what stops an ambient `HTTP_PROXY` silently routing the shared harvest through one
user's proxy.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from datetime import datetime, timezone
from typing import Any

import httpx

from services.harvester.fetch import make_shared_client
from services.harvester.sources.base import JobLink
from libs.corpus.filters import parse_date

SOURCE = "hiringcafe"
_TIMEOUT = 40
_MAX_PAGES = 3            # bound bandwidth: hiring.cafe is relevance-sorted, so we scan the top few
_POSTED_WITHIN_DAYS = 21  # default freshness window (by POSTING date) — override via config
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)
_PAGE_SIZE = 40

# Geocoded location objects captured from hiring.cafe's own filter parser (parse-filters). Keyed by
# the human name used in config. `workplace_types` is set per query (remote-only vs all). If you
# need another location, query hiring.cafe/api/ai-search/parse-filters?query=<place> and copy the
# object from suggestedFilters.locations[0].
_LOCATIONS: dict[str, dict[str, Any]] = {
    "europe": {"types": ["continent"], "formatted_address": "Europe", "address_components": [],
               "options": {}, "id": "Europecontinent"},
    "united kingdom": {"types": ["country"],
                       "address_components": [{"long_name": "United Kingdom", "short_name": "GB", "types": ["country"]}],
                       "geometry": {"location": {"lat": 55.3781, "lon": -3.4359}},
                       "formatted_address": "United Kingdom",
                       "options": {"flexible_regions": ["anywhere_in_continent", "anywhere_in_world"],
                                   "radius": 50, "radius_unit": "miles", "ignore_radius": False},
                       "id": "ehY1yZQBoEtHp_8UEq3V"},
    "budapest": {"types": ["locality"],
                 "address_components": [{"long_name": "Budapest", "short_name": "Budapest", "types": ["locality"]},
                                        {"long_name": "Budapest", "short_name": "Budapest", "types": ["administrative_area_level_1"]},
                                        {"long_name": "Hungary", "short_name": "HU", "types": ["country"]}],
                 "geometry": {"location": {"lat": 47.497912, "lon": 19.040235}},
                 "formatted_address": "Budapest, Hungary",
                 "options": {"radius": 50, "radius_unit": "miles", "ignore_radius": False},
                 "id": "HRY1yZQBoEtHp_8UErPW"},
}


def _to_joblink(hit: dict[str, Any]) -> JobLink | None:
    url = (hit.get("apply_url") or "").strip()
    if not url or hit.get("is_expired"):
        return None
    v5 = hit.get("v5_processed_job_data") or {}
    ji = hit.get("job_information") or {}
    title = (ji.get("title") or v5.get("core_job_title") or "").strip()
    # workplace_type is "Remote" / "Hybrid" / "Onsite". Hybrid must NOT map to is_remote=False
    # (the workplace filter would read that as on-site and drop it) — tag the location instead
    # so the classifier and the feed card both see it.
    wt = (v5.get("workplace_type") or "").strip().lower()
    is_remote = True if wt == "remote" else (False if wt == "onsite" else None)
    loc = (v5.get("formatted_workplace_location") or "").strip()
    if wt == "hybrid":
        loc = f"{loc} (Hybrid)".strip()
    # No raw JD in the hit — compose a description from the structured fields hiring.cafe provides.
    parts = [v5.get("requirements_summary") or ""]
    acts, tools = v5.get("role_activities"), v5.get("technical_tools")
    if isinstance(acts, list) and acts:
        parts.append("Responsibilities: " + ", ".join(str(a) for a in acts[:12]))
    if isinstance(tools, list) and tools:
        parts.append("Tools: " + ", ".join(str(t) for t in tools[:20]))
    snippet = "\n".join(p for p in parts if p)
    return JobLink(url=url, title=title, company=(v5.get("company_name") or "").strip(),
                   location=loc, source=SOURCE,
                   date_posted=(v5.get("estimated_publish_date") or None), snippet=snippet,
                   is_remote=is_remote)


def _recent(date_str: str | None, within_days: int) -> bool:
    """True if the posting is within `within_days` calendar days. hiring.cafe's dates are day-only.
    Unknown/unparseable date -> keep (fail open). This is the REAL recency control: the API's
    dateFetchedPastNDays filters by CRAWL date, so old-but-recrawled listings (posted years ago)
    slip through; we gate on the actual estimated posting date here."""
    dt = parse_date(date_str)
    if not dt:
        return True
    return (datetime.now(timezone.utc).date() - dt.date()).days <= within_days


def _search(client: httpx.Client, state: dict[str, Any], limit: int, within_days: int,
            seen: set[str], logger, max_pages: int = _MAX_PAGES) -> list[JobLink]:
    # hiring.cafe is RELEVANCE-sorted with no date sort, so fresh jobs are scattered — we scan the
    # top _MAX_PAGES and keep only those posted within the window. It's a coverage source, not a
    # freshness one; deep-paginating for the absolute newest isn't worth the bandwidth.
    from . import ats
    found: list[JobLink] = []
    enc = urllib.parse.quote(json.dumps(state))   # state is fixed for the query — encode once
    for page in range(max_pages):
        r = client.get(f"https://hiring.cafe/?searchState={enc}&page={page}")
        if r.status_code != 200:
            logger.warning("hiringcafe: HTTP %s for %r", r.status_code, state.get("searchQuery"))
            break
        m = _NEXT_DATA.search(r.text)
        if not m:
            break
        pp = json.loads(m.group(1)).get("props", {}).get("pageProps", {})
        hits = pp.get("ssrHits") or []
        try:
            ats.harvest_from_hits(hits)   # self-populate the greenhouse/ashby/lever token list (free)
        except Exception:
            pass
        for hit in hits:
            jl = _to_joblink(hit)
            if jl and jl.url not in seen and _recent(jl.date_posted, within_days):
                seen.add(jl.url)
                found.append(jl)
        if len(found) >= limit or pp.get("ssrIsLastPage") or not pp.get("ssrHits"):
            break
    return found[:limit]


def gather_hiringcafe(cfg: dict[str, Any], logger) -> list[JobLink]:
    """Adapter entry point (source key 'hiringcafe'). Never raises."""
    queries = cfg.get("queries", []) or []
    results_wanted = int(cfg.get("results_wanted", _PAGE_SIZE))
    # Freshness by POSTING date (the real control). dateFetchedPastNDays (crawl date) barely
    # filters — it returns old-but-recrawled listings — so we keep it wide and gate on posting date.
    within_days = int(cfg.get("posted_within_days", _POSTED_WITHIN_DAYS))
    max_pages = int(cfg.get("max_pages", _MAX_PAGES))   # seed_tokens --deep raises this
    out: list[JobLink] = []
    seen: set[str] = set()
    with make_shared_client() as client:
        for q in queries:
            loc_key = (q.get("location") or "europe").strip().lower()
            loc = _LOCATIONS.get(loc_key)
            if not loc:
                logger.warning("hiringcafe: unknown location %r (known: %s) — skipping query",
                               loc_key, ", ".join(_LOCATIONS))
                continue
            loc = {**loc, "workplace_types": (["Remote"] if q.get("is_remote") else [])}
            state = {"locations": [loc], "searchQuery": (q.get("search_term") or "").strip(),
                     "dateFetchedPastNDays": max(within_days, 7)}
            try:
                out.extend(_search(client, state, results_wanted, within_days, seen, logger,
                                   max_pages=max_pages))
            except Exception as exc:
                logger.warning("hiringcafe query %r failed: %s", q.get("search_term"), exc)
    return out
