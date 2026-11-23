"""Tier 2 — Remote job-board REST APIs and RSS feeds via httpx.

Field maps and quirks below were confirmed against the live APIs (2026-06/07):

  Source           Call                                          date field (format)           url
  ───────────────  ───────────────────────────────────────────  ────────────────────────────  ────────────────────────────────────
  Himalayas        GET  jobs/api?limit=                          pubDate (epoch s)              job["applicationLink"]  (remote-focused)
  JustJoin.it      GET  candidate-api/offers?from=&itemsCount= (newest first)  publishedAt (ISO)  justjoin.it/job-offer/slug workplaceType
  NoFluffJobs      POST search/posting (rawSearch body)          posted (epoch ms)              nofluffjobs.com/job/url fullyRemote=bool
  WorkingNomads    GET  RSS feed per category                    pubDate (RFC 2822)             <link>                  (all remote)
  Profession.hu    GET  /allasok/1,0,0,<kw>?rss (keyword RSS)    pubDate (RFC 2822)             <guid>                  (HU on-site mostly)
  Arbeitnow        GET  api/job-board-api?page= (no date order)   created_at (epoch s)           job["url"]              (Europe, DE most)
  Remote OK        GET  api (the whole board, its terms first)   date (ISO)                     job["url"]              (all remote)
  Platsbanken      GET  search?published-after=&sort=pubdate-desc  publication_date (Stockholm)  webpage_url             (Sweden, CC0)
  Reed             GET  api/1.0/search?resultsToSkip= (own order) date (dd/mm/yyyy, no time)     jobUrl                  (UK, key)

Every adapter applies the client-side recency filter (max_age_hours) on the
source's own date field, never raises (logs + returns [] on failure), and caps
output so one source can't flood the queue.

**Five adapters were removed on 2026-08-04** — Remotive, Arbeitnow, RemoteOK, WeWorkRemotely and
DreamJobs — along with the VC-board module. Each was pinged from the production box with a real
keyword, an unrelated one and nonsense, and answered identically to all three, or had stopped
publishing. A source that cannot be asked for anything is not part of a targeted harvest
(ROADMAP 6t), and an adapter nothing calls reads as current. They are recoverable from this
commit if any of them is ever brought back as a pure feed. Arbeitnow and Remote OK were, on 2026-11-22, no board
being asked a word since 2026-11-04. Remotive stays out: its terms forbid showing its jobs on another job site, and
it holds them back a day.

--- ported from the reference instance, 2026-07-27 ---

Lifted from `job-search/pipeline/sources/http_apis.py` by
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
import os
import re
import defusedxml.ElementTree as ET   # not stdlib ET: these parse third-party feeds
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime, parsedate_to_datetime
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from libs.corpus.filters import epoch_to_iso, is_within_hours
from libs.corpus.postings import strip_html
from libs.settings import DEFAULT_SOURCE_QUERIES_PER_CYCLE
from services.harvester.fetch import make_shared_client
from services.harvester.sources.base import JobLink, turn

# What a JSON API is asked for; the rest of every request's headers is `PAGE_HEADERS` (`fetch._options`).
_JSON = {"Accept": "application/json"}
_TIMEOUT = httpx.Timeout(30.0)


# Every board here is read whole: each job it lists in its window is kept, and people's words are matched
# on the stored jobs, title and text (owner, 2026-11-04). None is asked for a word or filtered by one.


# ── Himalayas ────────────────────────────────────────────────────────

# Himalayas answers at most 20 jobs a page, whatever `limit` asks for (measured 2026-11-03).
HIMALAYAS_PAGE = 20


def gather_himalayas(cfg: dict[str, Any], logger) -> list[JobLink]:
    """Himalayas' whole list, newest first, 20 a page, until a page is older than the window or
    `max_pages` (`source_pages_per_cycle`) are read: ~2,000 new jobs a day, so five pages are its last
    hour (measured 2026-11-03), and a run comes every fifteen minutes. A search per word cost a request per
    word, 20 jobs each; the list costs the same however many words there are.
    """
    endpoint = cfg.get("endpoint", "https://himalayas.app/jobs/api")
    max_age = cfg.get("max_age_hours", 24)
    out: list[JobLink] = []
    with make_shared_client() as client:
        for page in range(int(cfg.get("max_pages", 1))):
            try:
                jobs = client.get(endpoint, params={"limit": HIMALAYAS_PAGE, "offset": page * HIMALAYAS_PAGE}
                                  ).raise_for_status().json().get("jobs", [])
            except Exception as exc:
                logger.warning("himalayas page %d failed: %s", page, exc)
                break
            for j in jobs:
                iso = epoch_to_iso(j.get("pubDate"))
                if not is_within_hours(iso, max_age):
                    continue
                text = _striptags(j.get("description")) or j.get("excerpt", "")
                url = j.get("applicationLink") or j.get("guid")
                if not url:
                    continue
                loc = j.get("locationRestrictions") or []
                out.append(JobLink(
                    url=url, title=j.get("title", ""), company=j.get("companyName", ""),
                    location=", ".join(loc) if isinstance(loc, list) else str(loc),
                    source="himalayas", date_posted=iso, snippet=text,
                    is_remote=True,
                ))
            if len(jobs) < HIMALAYAS_PAGE or not is_within_hours(epoch_to_iso(jobs[-1].get("pubDate")), max_age):
                break
    return out


# ── JustJoin.it (v2 cursor API) ──────────────────────────────────────

def gather_justjoinit(cfg: dict[str, Any], logger) -> list[JobLink]:
    """Just Join IT's own offer API, newest first, `itemsCount` a page from the cursor `from`.

    Moved 2026-11-02: `api.justjoin.it/v2/user-panel/offers/by-cursor` has answered 503 to everything
    since 2026-11-01, and the site itself reads `justjoin.it/api/candidate-api/offers`, which carries the
    same records. About ten offers go up an hour, so the walk goes page by page until an offer is older
    than the window, at most `max_pages` (`source_pages_per_cycle`). A page that fails keeps the ones
    already read."""
    endpoint = cfg.get("endpoint", "https://justjoin.it/api/candidate-api/offers")
    max_age = cfg.get("max_age_hours", 24)
    per_page = int(cfg.get("per_page", 100))
    out: list[JobLink] = []
    data: list[dict] = []
    try:
        with make_shared_client() as client:
            for page in range(int(cfg.get("max_pages", 1))):
                batch = client.get(endpoint, params={"from": page * per_page, "itemsCount": per_page}
                                   ).raise_for_status().json().get("data", [])
                data += batch
                if len(batch) < per_page or not is_within_hours(batch[-1].get("publishedAt"), max_age):
                    break
    except Exception as exc:
        logger.warning("justjoinit fetch failed after %d offer(s): %s", len(data), exc)
    for j in data:
        posted = j.get("publishedAt") or j.get("lastPublishedAt")
        if not is_within_hours(posted, max_age):
            continue
        skills = " ".join(s.get("name", "") for s in (j.get("requiredSkills") or []) if isinstance(s, dict))
        slug = j.get("slug")
        if not slug:
            continue
        # workplaceType is exact: remote / hybrid / office. Hybrid must not become
        # is_remote=False (reads as on-site) — tag the location instead.
        wt = (j.get("workplaceType") or "").lower()
        loc = j.get("city", "") or ""
        if wt == "hybrid":
            loc = f"{loc} (Hybrid)".strip()
        out.append(JobLink(
            url=f"https://justjoin.it/job-offer/{slug}", title=j.get("title", ""),
            company=j.get("companyName", ""), location=loc,
            source="justjoinit", date_posted=posted, snippet=skills,
            is_remote=(True if wt == "remote" else (False if wt == "office" else None)),
        ))
    return out


# ── NoFluffJobs (POST search) ────────────────────────────────────────

def gather_nofluffjobs(cfg: dict[str, Any], logger) -> list[JobLink]:
    """No Fluff Jobs' whole list, newest first by when each posting went up or was last renewed: 19,629
    postings over 130 pages, and every one renewed in the last hour on the first four (measured
    2026-11-04; the whole list read in 36 s). A run walks from the first page until a page has nothing
    renewed inside the window, at most `max_pages`, and keeps every posting that went up inside it. The
    page number goes in the address; in the body it is ignored. Config keys: `cities` (optional),
    `source_label` (default nofluffjobs).
    """
    endpoint = cfg.get("endpoint", "https://nofluffjobs.com/api/search/posting")
    max_age = cfg.get("max_age_hours", 24)
    cities = cfg.get("cities") or []
    label = cfg.get("source_label", "nofluffjobs")
    criteria: dict[str, Any] = {"city": [c.lower() for c in cities]} if cities else {}
    postings: list[dict] = []
    with make_shared_client(headers={**_JSON, "Content-Type": "application/json"}, follow_redirects=True) as client:
        for page in range(1, int(cfg.get("max_pages", 1)) + 1):
            try:
                batch = client.post(endpoint, params={"page": page, "salaryCurrency": cfg.get("currency", "PLN"),
                                                      "salaryPeriod": "month", "region": "pl"},
                                    json={"criteriaSearch": criteria, "page": page}).raise_for_status().json().get("postings") or []
            except Exception as exc:
                logger.warning("%s page %d failed: %s", label, page, exc)
                break
            postings += batch
            if not any(is_within_hours(epoch_to_iso(j.get("renewed") or j.get("posted")), max_age) for j in batch):
                break
    out: list[JobLink] = []
    seen: set[str] = set()
    for j in postings:
        slug = j.get("url")
        iso = epoch_to_iso(j.get("posted"))
        if not slug or slug in seen or not is_within_hours(iso, max_age):
            continue
        seen.add(slug)
        title = j.get("title", "")
        loc = j.get("location") or {}
        places = loc.get("places", []) if isinstance(loc, dict) else []
        city = places[0].get("city", "") if places and isinstance(places[0], dict) else ""
        # e.g. ", Hungary" on the HU block, so the country filter can place the job.
        if city and cfg.get("location_suffix"):
            city += cfg["location_suffix"]
        out.append(JobLink(
            url=f"https://nofluffjobs.com/job/{slug}", title=title,
            company=j.get("name", ""), location=city, source=label,
            date_posted=iso, snippet=title,
            # fullyRemote=false could be hybrid — unknown, not on-site.
            is_remote=(True if j.get("fullyRemote") else None),
        ))
    return out


# ── RSS date helper ───────────────────────────────────────────────────

def _rss_date_to_iso(date_str: str | None) -> str | None:
    """Convert RFC 2822 RSS pubDate to ISO-8601 string (returns None on failure)."""
    if not date_str:
        return None
    try:
        return parsedate_to_datetime(date_str.strip()).isoformat()
    except Exception:
        return date_str  # pass through to is_within_hours which handles various formats



# ── RSS parsing (tolerant) ────────────────────────────────────────────
_RSS_ENTITY_RE = re.compile(r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9a-fA-F]+;)")
_RSS_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _rss_root(resp, feed_url: str, logger, label: str):
    """Parse an RSS body into an ElementTree root, tolerating the malformed feeds seen in the
    wild: bare `&` ampersands (WorkingNomads), stray control chars, a BOM, or an empty body
    (WeWorkRemotely sometimes returns nothing). Returns None on failure — logged, so the caller
    just skips that feed instead of raising. Empty bodies log at INFO (transient, not a bug);
    only genuinely unparseable XML (after a repair pass) warns."""
    text = (resp.text or "").lstrip("﻿").strip()
    if not text:
        logger.info("%s feed %s: empty response (HTTP %s) — skipping", label, feed_url, resp.status_code)
        return None
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        # Repair pass: escape bare ampersands + drop invalid XML control chars, then retry once.
        repaired = _RSS_CTRL_RE.sub("", _RSS_ENTITY_RE.sub("&amp;", text))
        try:
            return ET.fromstring(repaired)
        except ET.ParseError as exc:
            logger.warning("%s feed %s: unparseable RSS even after repair (%s) — skipping",
                           label, feed_url, exc)
            return None


# ── generic RSS gather (WeWorkRemotely + Working Nomads share everything but the
#    title format, so one loop + a per-source title parser) ─────────────────────

def gather_workingnomads(cfg: dict[str, Any], logger) -> list[JobLink]:
    """Working Nomads public JSON API (/api/exposed_jobs/) — all-remote board.

    Reprobed 2026-07-23: the old RSS feeds (`?category=...&format=rss`) now 301-redirect to HTML
    and no longer parse. `GET /api/exposed_jobs/` returns the full current listing (~44 jobs) as a
    JSON array — no auth. Field map: `url` (direct), `title`, `company_name`, `description` (HTML),
    `tags` (comma string), `location` (free text, e.g. "REMOTE, Canada"/"Germany"/"Global"),
    `pub_date` ISO-8601 with tz offset. Every job is kept, its words matched here; recency on
    pub_date. is_remote=True (remote-only board)."""
    endpoint = cfg.get("endpoint", "https://www.workingnomads.com/api/exposed_jobs/")
    max_age = cfg.get("max_age_hours", 24)
    out: list[JobLink] = []
    try:
        with make_shared_client() as client:
            data = client.get(endpoint).raise_for_status().json()
    except Exception as exc:
        logger.warning("workingnomads fetch failed: %s", exc)
        return out
    for j in (data if isinstance(data, list) else []):
        iso = j.get("pub_date")
        if not is_within_hours(iso, max_age):
            continue
        title = (j.get("title") or "").strip()
        desc = _striptags(j.get("description"))
        url = (j.get("url") or "").strip()
        if not url:
            continue
        out.append(JobLink(
            url=url, title=title, company=(j.get("company_name") or "").strip(),
            location=(j.get("location") or "Remote").strip(), source="workingnomads",
            date_posted=iso, snippet=desc, is_remote=True))
    return out


def _striptags(html: str | None) -> str:
    """An HTML fragment as readable text, its paragraphs and list items kept (job descriptions come
    as HTML). The one way the harvester reads markup, so every board's text is laid out alike."""
    return strip_html(html or "")


# ── Jobicy ────────────────────────────────────────────────────────────

def gather_jobicy(cfg: dict[str, Any], logger) -> list[JobLink]:
    """Jobicy public JSON API (jobicy.com/api/v2/remote-jobs) — free, no auth, all remote.

    One GET per configured tag (?tag=<t>&count=<n>), the newest first: 100 reach back about six hours
    (measured 2026-11-03). Every job is kept. Where it may be done (`jobGeo`, "USA" or "Anywhere") is
    its location, which each person's countries judge: a fixed list of regions to keep, the reference
    instance's own Europe and Hungary, dropped every other person's.
    """
    endpoint = cfg.get("endpoint", "https://jobicy.com/api/v2/remote-jobs")
    max_age = cfg.get("max_age_hours", 24)
    count = int(cfg.get("count", 50))
    tags: list[str] = cfg.get("tags", []) or [""]
    out: list[JobLink] = []
    seen: set[str] = set()
    with make_shared_client() as client:
        for tag in tags:
            params = {"count": count}
            if tag:
                params["tag"] = tag
            try:
                jobs = client.get(endpoint, params=params).raise_for_status().json().get("jobs", [])
            except Exception as exc:
                logger.warning("jobicy tag %r failed: %s", tag, exc)
                continue
            for j in jobs:
                url = j.get("url")
                if not url or url in seen:
                    continue
                iso = j.get("pubDate")
                if not is_within_hours(iso, max_age):
                    continue
                # The whole text: the excerpt is 150 characters, and kept as the job's text it was never
                # read in full, as anything past `MIN_DESCRIPTION` counts as described.
                text = _striptags(j.get("jobDescription") or j.get("jobExcerpt"))
                title = j.get("jobTitle", "")
                seen.add(url)
                out.append(JobLink(
                    url=url, title=title, company=j.get("companyName", ""),
                    location=j.get("jobGeo") or "Remote", source="jobicy",
                    date_posted=iso, snippet=text, is_remote=True,
                ))
    return out


# ── Findwork.dev ──────────────────────────────────────────────────────

def gather_findwork(cfg: dict[str, Any], logger) -> list[JobLink]:
    """Findwork.dev API (findwork.dev/api/jobs/) — dev-focused, remote flag. Requires a FREE
    token: set FINDWORK_API_KEY in .env. Disabled (returns []) when the key is absent."""
    token = os.environ.get("FINDWORK_API_KEY") or cfg.get("api_key") or ""
    if not token:
        logger.info("findwork: no FINDWORK_API_KEY set — skipping (free key at findwork.dev/developers/ after sign-in)")
        return []
    endpoint = cfg.get("endpoint", "https://findwork.dev/api/jobs/")
    max_age = cfg.get("max_age_hours", 24)
    headers = {**_JSON, "Authorization": f"Token {token}"}
    out: list[JobLink] = []
    seen: set[str] = set()
    # Its whole list, newest first, page by page until a page is older than the window, at most
    # `max_pages`. It was asked for "python", "backend" and "golang" only. Unmeasured: no key here.
    with make_shared_client(headers=headers, follow_redirects=True) as client:
        for page in range(1, int(cfg.get("max_pages", 1)) + 1):
            try:
                results = client.get(endpoint, params={"sort_by": "date", "page": page}).raise_for_status().json().get("results", [])
            except Exception as exc:
                logger.warning("findwork page %d failed: %s", page, exc)
                break
            for j in results:
                url = (j.get("url") or "").strip()
                if not url or url in seen:
                    continue
                iso = j.get("date_posted")
                if not is_within_hours(iso, max_age):
                    continue
                seen.add(url)
                out.append(JobLink(
                    url=url, title=j.get("role", ""), company=j.get("company_name", ""),
                    location=j.get("location") or "Remote", source="findwork",
                    date_posted=iso, snippet=_striptags(j.get("text")),
                    is_remote=(True if j.get("remote") else None),
                ))
            if not results or not is_within_hours(results[-1].get("date_posted"), max_age):
                break
    return out


# ── Adzuna ────────────────────────────────────────────────────────────

# The countries Adzuna actually serves. A vendor fact, so it lives beside the vendor's adapter:
# asking for a code outside this list is a 404, and the user-facing location list is much wider
# (`libs.corpus.geo` knows all 27 EU members; Adzuna covers eight of them).
ADZUNA_COUNTRIES: frozenset[str] = frozenset({
    "at", "au", "be", "br", "ca", "ch", "de", "es", "fr", "gb", "in", "it",
    "mx", "nl", "nz", "pl", "sg", "us", "za",
})

# The ceiling comes from `libs.settings`, which is where the operator's value lives too. A constant
# here as well would be the defect this project ships most often: the panel showing one number
# while the code reads another, with nothing erroring.


def gather_adzuna(cfg: dict[str, Any], logger) -> list[JobLink]:
    """Adzuna aggregator API — broad EU coverage, native recency (max_days_old). Requires FREE
    credentials: set ADZUNA_APP_ID and ADZUNA_APP_KEY in .env. Disabled when either is absent.

    One GET per country somebody picked, with no word: its newest `results_per_page` jobs. The free
    tier allows ~250 calls a day against a run every fifteen minutes, so `max_queries` countries are
    asked a run and the rest on the next (`turn`): a whole country is more than the tier can read, so
    each is read at its newest. Unmeasured: no key here. is_remote is left None (Adzuna has no exact
    remote flag) so the workplace filter decides."""
    app_id = os.environ.get("ADZUNA_APP_ID") or cfg.get("app_id") or ""
    app_key = os.environ.get("ADZUNA_APP_KEY") or cfg.get("app_key") or ""
    if not (app_id and app_key):
        logger.info("adzuna: ADZUNA_APP_ID/ADZUNA_APP_KEY not set — skipping (free key at developer.adzuna.com)")
        return []
    max_age = cfg.get("max_age_hours", 24)
    per_page = int(cfg.get("results_per_page", 50))
    countries: list[str] = [c for c in cfg.get("countries") or [] if c in ADZUNA_COUNTRIES]
    if not countries:
        logger.warning("adzuna: nobody has picked a country Adzuna serves — asking for nothing this cycle")
        return []
    budget = int(cfg.get("max_queries") or DEFAULT_SOURCE_QUERIES_PER_CYCLE["adzuna"])
    asked = [countries[index] for index in turn(len(countries), budget)]

    max_days = max(1, round(max_age / 24))
    out: list[JobLink] = []
    seen: set[str] = set()
    with make_shared_client(headers=_JSON, follow_redirects=True) as client:
        for country in asked:
            params = {"app_id": app_id, "app_key": app_key, "results_per_page": per_page,
                      "max_days_old": max_days, "sort_by": "date", "content-type": "application/json"}
            try:
                results = client.get(f"https://api.adzuna.com/v1/api/jobs/{country}/search/1",
                                     params=params).raise_for_status().json().get("results", [])
            except Exception as exc:
                logger.warning("adzuna %s failed: %s", country, exc)
                continue
            for j in results:
                url = (j.get("redirect_url") or "").strip()
                if not url or url in seen:
                    continue
                iso = j.get("created")
                if not is_within_hours(iso, max_age):
                    continue
                seen.add(url)
                out.append(JobLink(
                    url=url, title=j.get("title", ""),
                    company=(j.get("company") or {}).get("display_name", ""),
                    location=(j.get("location") or {}).get("display_name", ""),
                    source="adzuna", date_posted=iso,
                    snippet=_striptags(j.get("description")), is_remote=None,
                ))
    return out


# ── Profession.hu (Hungary's market-leading board, ~80% share) ────────

# Major Hungarian cities as they appear in profession.hu ad slugs (accent-stripped).
# The token before the ad id is only trusted as a city when it's on this list —
# otherwise it can be the company-name tail (e.g. "...grafton-recruitment-2945977").
_HU_CITIES = {
    "budapest", "debrecen", "szeged", "miskolc", "pecs", "gyor", "nyiregyhaza",
    "kecskemet", "szekesfehervar", "szombathely", "szolnok", "tatabanya", "kaposvar",
    "erd", "veszprem", "bekescsaba", "zalaegerszeg", "sopron", "eger", "nagykanizsa",
    "dunaujvaros", "hodmezovasarhely", "salgotarjan", "cegled", "baja", "vac",
    "godollo", "mosonmagyarovar", "esztergom", "budaors", "paks", "gyongyos",
    "szentendre", "siofok", "hatvan", "komarom", "tata", "papa", "varpalota",
}

def gather_profession(cfg: dict[str, Any], logger) -> list[JobLink]:
    """Profession.hu's RSS feed — the dominant Hungarian general job board.

    `/allasok/1,0,0,<keyword>?rss` is an RSS 2.0 feed of the 15 newest ads with that word, and with
    no word, of the 15 newest ads of every kind: they reach back about three hours (measured
    2026-11-03), and a run comes every fifteen minutes. So one request a run reads all of it, where a
    request per word read only the words somebody had, and cost more with every new one. There is no
    pagination: the page number in the URL is ignored. Field map: <guid> is the clean ad URL (the <link> variant
    carries `?utm_source=rss` plus a trailing space — use guid); <pubDate> is RFC 2822;
    <description> is a JSON array of highlight bullets. The feed has NO company field —
    company stays empty and the description fetch at scoring time fills the JD.
    Location is parsed from the ad slug (`...-<company>-<city>-<id>`): the token before
    the trailing numeric id is the city. Ads are mostly Hungarian-language (JOB_LANGUAGES
    keeps hu) and mostly on-site Hungary, which the workplace filter allows. is_remote:
    True only on an explicit remote word in the title; hybrid tags the location."""
    base = cfg.get("endpoint", "https://www.profession.hu/allasok/1,0,0,{kw}?rss")
    max_age = cfg.get("max_age_hours", 24)
    out: list[JobLink] = []
    seen: set[str] = set()
    # Not a browser's User-Agent: Profession answers one with a 500 page and the feed to anything else
    # (measured 2026-11-03, 15 items either side of it). The one adapter that says who it is.
    feed = {"User-Agent": "Mozilla/5.0 (compatible; job-search-bot/1.0)",
            "Accept": "application/rss+xml, text/xml, application/xml"}
    feed_url = base.format(kw="")
    try:
        with make_shared_client(headers=feed, follow_redirects=True) as client:
            resp = client.get(feed_url)
    except Exception as exc:
        logger.warning("profession feed failed: %s", exc)
        return out
    root = _rss_root(resp, feed_url, logger, "profession")
    if root is None:
        return out
    for item in root.findall(".//item"):
        url = (item.findtext("guid") or item.findtext("link") or "").strip()
        url = url.split("?", 1)[0]
        if not url or url in seen:
            continue
        pub_date = _rss_date_to_iso(item.findtext("pubDate"))
        if not is_within_hours(pub_date, max_age):
            continue
        seen.add(url)
        title = (item.findtext("title") or "").strip()
        low = title.lower()
        # Slug shape: .../allas/<title-company>-<city>-<id>[/pro] — city precedes the
        # numeric id; some ads append a trailing "/pro" segment, so scan path segments
        # for the one that ends in the id. The token is only a city if it's a known HU
        # city — otherwise it's usually the company-name tail (fails open to "Hungary").
        city = ""
        for seg in reversed(url.rstrip("/").split("/")):
            tokens = seg.split("-")
            if len(tokens) >= 2 and tokens[-1].isdigit():
                if tokens[-2] in _HU_CITIES:
                    city = tokens[-2].capitalize()
                break
        location = f"{city}, Hungary" if city else "Hungary"
        if "hibrid" in low or "hybrid" in low:
            location += " (Hybrid)"
        # Description is a JSON array of highlight bullets.
        desc_raw = (item.findtext("description") or "").strip()
        try:
            bullets = json.loads(desc_raw)
            snippet = "\n".join(f"• {b}" for b in bullets) if isinstance(bullets, list) else desc_raw
        except Exception:
            snippet = _striptags(desc_raw)
        out.append(JobLink(
            url=url, title=title, company="", location=location,
            source="profession", date_posted=pub_date,
            snippet=snippet or title,
            # Explicit remote word only; absence is unknown, and unknown is kept.
            is_remote=(True if ("remote" in low or "távmunka" in low) else None),
        ))
    return out


# ── Arbeitnow ─────────────────────────────────────────────────────────

def gather_arbeitnow(cfg: dict[str, Any], logger) -> list[JobLink]:
    """Arbeitnow's free API: jobs across Europe, Germany's most, 300 a page with the whole text, no key. Its terms:
    "a free public API for jobs, please do not abuse", a link back appreciated (a card's "Apply on Arbeitnow").

    Its pages are in no date order: page 2 held a job 0.9 hours old beside ones 83 hours old, and every job listed
    in the last hour was on the first two (53 and 9 of 62; measured 2026-11-22). So a run reads the first
    `max_pages` whatever their dates say, keeping each job listed inside the window, and a page that fails keeps
    the ones before it."""
    max_age = cfg.get("max_age_hours", 24)
    out: list[JobLink] = []
    with make_shared_client(headers=_JSON) as client:
        for page in range(1, int(cfg.get("max_pages", 1)) + 1):
            try:
                body = client.get("https://www.arbeitnow.com/api/job-board-api", params={"page": page}
                                  ).raise_for_status().json()
            except Exception as exc:
                logger.warning("arbeitnow page %d failed: %s", page, exc)
                break
            for j in body.get("data") or []:
                iso = epoch_to_iso(j.get("created_at"))
                if j.get("url") and is_within_hours(iso, max_age):
                    out.append(JobLink(
                        url=j["url"], title=j.get("title", ""), company=j.get("company_name", ""),
                        location=j.get("location", ""), source="arbeitnow", date_posted=iso,
                        snippet=_striptags(j.get("description")),
                        # Not remote could be hybrid: unknown, not on-site.
                        is_remote=True if j.get("remote") else None))
            if not (body.get("links") or {}).get("next"):
                break
    return out


# ── Remote OK ─────────────────────────────────────────────────────────

def gather_remoteok(cfg: dict[str, Any], logger) -> list[JobLink]:
    """Remote OK's free API: its whole board, newest first with each job's text, in one request (100 jobs over a
    day and a half, 2026-11-22). The first item is its terms: a link back to the job on Remote OK and its name
    shown, which a card's "Apply on Remote OK" is (`libs.corpus.boards`)."""
    max_age = cfg.get("max_age_hours", 24)
    try:
        with make_shared_client(headers=_JSON) as client:
            data = client.get("https://remoteok.com/api").raise_for_status().json()
    except Exception as exc:
        logger.warning("remoteok fetch failed: %s", exc)
        return []
    return [JobLink(url=j["url"], title=j.get("position", ""), company=j.get("company", ""),
                    location=j.get("location") or "Remote", source="remoteok", date_posted=j.get("date"),
                    snippet=_striptags(j.get("description")), is_remote=True)
            for j in data if isinstance(j, dict) and j.get("url") and is_within_hours(j.get("date"), max_age)]


# ── Platsbanken (Sweden's public employment service) ─────────────────

# Its times are Stockholm's, written without an offset: an ad read at 19:51 UTC said it went up at 20:36
# (2026-11-22). The search reads no further than its 2,000th ad.
_STOCKHOLM = ZoneInfo("Europe/Stockholm")
PLATSBANKEN_PAGE, PLATSBANKEN_LAST = 100, 2000


def gather_platsbanken(cfg: dict[str, Any], logger) -> list[JobLink]:
    """Every ad Sweden's public employment service published in the window, newest first, 100 a page until a short
    one, each with its whole text and its own link to apply: JobTech's official JobSearch API, no key, the ads
    under CC0. 148 went up in the 27 hours to a Sunday evening (2026-11-22). The window is asked in minutes, so the
    API's clock decides it, not ours."""
    window = int(cfg.get("max_age_hours", 2)) * 60
    out: list[JobLink] = []
    with make_shared_client(headers=_JSON) as client:
        for page in range(min(int(cfg.get("max_pages", 1)), PLATSBANKEN_LAST // PLATSBANKEN_PAGE + 1)):
            try:
                hits = client.get("https://jobsearch.api.jobtechdev.se/search", params={
                    "published-after": window, "sort": "pubdate-desc", "limit": PLATSBANKEN_PAGE,
                    "offset": page * PLATSBANKEN_PAGE}).raise_for_status().json().get("hits") or []
            except Exception as exc:
                logger.warning("platsbanken page %d failed: %s", page, exc)
                break
            for ad in hits:
                if not ad.get("webpage_url"):
                    continue
                where = ad.get("workplace_address") or {}
                country = where.get("country")
                posted = ad.get("publication_date")
                apply = (ad.get("application_details") or {}).get("url") or ""
                out.append(JobLink(
                    url=ad["webpage_url"], title=ad.get("headline", ""),
                    company=(ad.get("employer") or {}).get("name", ""),
                    location=", ".join(part for part in (
                        where.get("municipality"), "Sweden" if country in (None, "", "Sverige") else country) if part),
                    source="platsbanken", snippet=_striptags((ad.get("description") or {}).get("text")),
                    date_posted=_in_stockholm(posted) if posted else None,
                    application_url=apply if apply.startswith("http") else None))
            if len(hits) < PLATSBANKEN_PAGE:
                break
    return out


def _in_stockholm(posted: str) -> str:
    """A Platsbanken time as one with its offset: Stockholm's where it gives none."""
    when = datetime.fromisoformat(posted)
    return (when if when.tzinfo else when.replace(tzinfo=_STOCKHOLM)).isoformat()


# ── Reed (the UK) ─────────────────────────────────────────────────────

# 100 a page, the most its search answers, in an order of its own: not by date, though most of its first pages had
# gone up that day (2026-11-23). It offers no filter by time and no sort.
REED_PAGE = 100


def gather_reed(cfg: dict[str, Any], logger) -> list[JobLink]:
    """Reed's jobseeker API: the UK's jobs with no search word, the first `max_pages` in its own order, each dated
    inside the window kept. Its key (`REED_API_KEY`) is added by the fetch layer, for its API alone. A job's date has
    no time, so a window counts from midnight. The search's text is a teaser of 450 characters ending "...", left
    out on purpose: the whole text is read from Reed's details API (`libs.corpus.postings`), with where the job is
    applied to."""
    max_age = cfg.get("max_age_hours", 48)
    out: list[JobLink] = []
    with make_shared_client(headers=_JSON) as client:
        for page in range(int(cfg.get("max_pages", 1))):
            try:
                results = client.get("https://www.reed.co.uk/api/1.0/search", params={
                    "resultsToTake": REED_PAGE, "resultsToSkip": page * REED_PAGE}
                ).raise_for_status().json().get("results") or []
            except Exception as exc:
                logger.warning("reed page %d failed: %s", page, exc)
                break
            for j in results:
                posted = _reed_day(j.get("date"))
                where = j.get("locationName") or ""
                if j.get("jobUrl") and is_within_hours(posted, max_age):
                    out.append(JobLink(
                        url=j["jobUrl"], title=j.get("jobTitle") or "", company=j.get("employerName") or "",
                        # A town the place list does not know is still in the UK, the only country Reed lists.
                        location=where if "united kingdom" in where.lower() else f"{where}, United Kingdom".lstrip(", "),
                        source="reed", date_posted=posted))
            if len(results) < REED_PAGE:
                break
    return out


def _reed_day(day: str | None) -> str | None:
    """Reed's "23/11/2026" as an ISO date, or `None` where it gives none."""
    try:
        return datetime.strptime(day or "", "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


# ── Teaching Vacancies (England's schools, the Department for Education) ─────

TEACHING_PAGE = 100


def gather_teachingvacancies(cfg: dict[str, Any], logger) -> list[JobLink]:
    """The Department for Education's open API of every school job in England: 100 a page, newest first, each a
    schema.org JobPosting with its whole text, no key, under the Open Government Licence (its one exception: no
    fee for contacting, interviewing or hiring somebody who answered a listing). 6,370 jobs on 2026-11-23, its
    first two pages that day's. A date has no time, so a window counts from midnight; the walk ends at the
    page that reaches past it."""
    max_age = cfg.get("max_age_hours", 48)
    out: list[JobLink] = []
    with make_shared_client(headers=_JSON) as client:
        for page in range(1, int(cfg.get("max_pages", 1)) + 1):
            try:
                jobs = client.get("https://teaching-vacancies.service.gov.uk/api/v1/jobs.json",
                                  params={"page": page}).raise_for_status().json().get("data") or []
            except Exception as exc:
                logger.warning("teachingvacancies page %d failed: %s", page, exc)
                break
            fresh = [j for j in jobs if j.get("url") and is_within_hours(j.get("datePosted"), max_age)]
            for j in fresh:
                town = (((j.get("jobLocation") or {}).get("address") or {}).get("addressLocality") or "").strip()
                out.append(JobLink(
                    url=j["url"], title=j.get("title") or "",
                    company=(j.get("hiringOrganization") or {}).get("name") or "",
                    location=f"{town}, United Kingdom".lstrip(", "), source="teachingvacancies",
                    date_posted=j.get("datePosted"), snippet=_striptags(j.get("description"))))
            if len(jobs) < TEACHING_PAGE or len(fresh) < len(jobs):
                break
    return out


# ── NAV Arbeidsplassen (Norway's public employment service) ───────────

NAV_FEED = "https://pam-stilling-feed.nav.no"


def gather_nav(cfg: dict[str, Any], logger) -> list[JobLink]:
    """Norway's job ads, most of those advertised in the country, from NAV's official feed: everything changed
    since the window (If-Modified-Since), page after page (86 changes in two hours of a Monday afternoon, 76
    live; 2026-11-23). Its terms let a job site show the ads, and ask that a withdrawn one leave at once: it is
    passed on closed. Its token (`NAV_API_TOKEN`, asked of NAV by email) is added by the fetch layer. A live ad's
    text, when it went up and its link to apply are read from its entry (`libs.corpus.postings`), once. Each
    change is an entry, so an ad changed twice in the window is in it twice (75 entries for 65 ads in two hours,
    2026-11-23): it is passed on once, as it stands at the last."""
    since = format_datetime(datetime.now(UTC) - timedelta(hours=int(cfg.get("max_age_hours", 2))), usegmt=True)
    address, headers, latest = f"{NAV_FEED}/api/v1/feed", {"If-Modified-Since": since}, {}
    with make_shared_client(headers=_JSON) as client:
        for _page in range(int(cfg.get("max_pages", 1))):
            try:
                page = client.get(address, headers=headers).raise_for_status().json()
            except Exception as exc:
                logger.warning("nav feed failed: %s", exc)
                break
            for item in page.get("items") or []:
                entry = item.get("_feed_entry") or {}
                if entry.get("uuid"):
                    latest[entry["uuid"]] = JobLink(
                        url=f"https://arbeidsplassen.nav.no/stillinger/stilling/{entry['uuid']}",
                        title=entry.get("title") or "", company=entry.get("businessName") or "",
                        location=f"{(entry.get('municipal') or '').title()}, Norway".lstrip(", "), source="nav",
                        closed=entry.get("status") != "ACTIVE")
            if not page.get("items") or not page.get("next_url"):
                break
            address, headers = f"{NAV_FEED}{page['next_url']}", {}
    return list(latest.values())


# ── Feina Activa (Catalonia's public employment service, the SOC) ─────

FEINA_ACTIVA = "https://feinaactiva.gencat.cat/api/offers/offers-xml"


def gather_feinaactiva(cfg: dict[str, Any], logger) -> list[JobLink]:
    """Catalonia's public employment service (the SOC): every offer it publishes, in one XML file made as it is
    asked, in two lists, its board's and its offices' (4,949 offers on 2026-11-23, 116 dated that day by 17:00).
    Under the Generalitat's open licence: reuse, commercial too, crediting the SOC and leaving the text as it
    is. 7.5 MB a read, not compressed and with nothing to ask from, so the whole file each run. A date has no
    time, so a window counts from midnight. Its text is the offer's own parts, one after another, contract and
    hours with them: 420 of 4,949 had under 200 characters without, 208 with."""
    try:
        with make_shared_client() as client:
            root = ET.fromstring(client.get(FEINA_ACTIVA).raise_for_status().content)
    except Exception as exc:
        logger.warning("feinaactiva failed: %s", exc)
        return []
    out: list[JobLink] = []
    for ad in root.iter("ad"):
        day, url = (ad.findtext("date") or "").strip().split("/"), (ad.findtext("url") or "").strip()
        posted = "-".join(reversed(day)) if len(day) == 3 else None
        if (not url.startswith("https://") or ad.findtext("status") != "PUBLISHED"
                or not is_within_hours(posted, cfg.get("max_age_hours", 48))):
            continue
        # The catalogue's way of writing a town with its article ("Albi, L'"), turned the way it is said.
        name, _, article = (ad.findtext("city") or "").strip().title().partition(", ")
        town = f"{article}{'' if article.endswith("'") else ' '}{name}" if article else name
        parts = ((ad.findtext(part) or "").strip()
                 for part in ("content", "experience", "requirements", "studies", "contract", "workingHours"))
        out.append(JobLink(url=url, title=(ad.findtext("title") or "").strip(),
                           company=(ad.findtext("company") or "").strip(), location=f"{town}, Spain".lstrip(", "),
                           source="feinaactiva", date_posted=posted, snippet="\n\n".join(p for p in parts if p)))
    return out
