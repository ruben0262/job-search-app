"""Tier 2 — Remote job-board REST APIs and RSS feeds via httpx.

Field maps and quirks below were confirmed against the live APIs (2026-06/07):

  Source           Call                                          date field (format)           url
  ───────────────  ───────────────────────────────────────────  ────────────────────────────  ────────────────────────────────────
  Remotive         GET  remote-jobs?search=&limit=               publication_date (ISO)         job["url"]              (all remote)
  Arbeitnow        GET  job-board-api?search=                    created_at (epoch s)           job["url"]              remote=bool
  Himalayas        GET  jobs/api?limit=                          pubDate (epoch s)              job["applicationLink"]  (remote-focused)
  JustJoin.it      GET  v2 by-cursor?perPage= (newest first)     publishedAt (ISO)              justjoin.it/job-offer/slug workplaceType
  NoFluffJobs      POST search/posting (rawSearch body)          posted (epoch ms)              nofluffjobs.com/job/url fullyRemote=bool
  RemoteOK         GET  api?tag=                                 date (ISO) / epoch (unix s)    job["url"]              (all remote)
  WeWorkRemotely   GET  RSS feed per category                    pubDate (RFC 2822)             <link>                  (all remote)
  WorkingNomads    GET  RSS feed per category                    pubDate (RFC 2822)             <link>                  (all remote)
  Profession.hu    GET  /allasok/1,0,0,<kw>?rss (keyword RSS)    pubDate (RFC 2822)             <guid>                  (HU on-site mostly)
  DreamJobs.hu     GET  api/v1/en/jobs?region=hu (paginated)     started_at (ISO, space sep)    /en/job/<co>/<slug>     (HU IT board)

Every adapter applies the client-side recency filter (max_age_hours) on the
source's own date field, never raises (logs + returns [] on failure), and caps
output so one source can't flood the queue.

--- ported from the reference instance, 2026-07-27 ---

Lifted from `job-search/pipeline/sources/http_apis.py` by
`services/harvester/sources/port_from_reference.py`. Parsing, pagination and per-source quirks are
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
import time
import defusedxml.ElementTree as ET   # not stdlib ET: these parse third-party feeds
from email.utils import parsedate_to_datetime
from typing import Any, Iterable
from urllib.parse import quote

import httpx

from libs.corpus.filters import epoch_to_iso, is_within_hours
from services.harvester.fetch import make_shared_client
from services.harvester.sources.base import JobLink

_UA = {"User-Agent": "Mozilla/5.0 (job-search-pipeline)", "Accept": "application/json"}
_TIMEOUT = httpx.Timeout(30.0)


def _keyword_hit(text: str, keywords: Iterable[str]) -> bool:
    low = text.lower()
    return any(k.lower() in low for k in keywords)


def _tokens(phrase: str) -> list[str]:
    """Significant words from a query phrase (len > 2), for lenient matching."""
    return [w for w in phrase.lower().split() if len(w) > 2]


# ── Remotive ─────────────────────────────────────────────────────────

def gather_remotive(cfg: dict[str, Any], logger) -> list[JobLink]:
    endpoint = cfg.get("endpoint", "https://remotive.com/api/remote-jobs")
    max_age = cfg.get("max_age_hours", 24)
    out: list[JobLink] = []
    with make_shared_client() as client:
        for q in cfg.get("queries", []):
            params = {"limit": 100, "search": q.get("search", "")}
            if q.get("category"):
                params["category"] = q["category"]
            try:
                jobs = client.get(endpoint, params=params).json().get("jobs", [])
            except Exception as exc:
                logger.warning("remotive query %s failed: %s", q, exc)
                continue
            for j in jobs:
                posted = j.get("publication_date")
                if not is_within_hours(posted, max_age):
                    continue
                url = j.get("url")
                if not url:
                    continue
                out.append(JobLink(
                    url=url, title=j.get("title", ""), company=j.get("company_name", ""),
                    location=j.get("candidate_required_location", ""), source="remotive",
                    date_posted=posted, snippet=j.get("description", ""), is_remote=True,
                ))
    return out


# ── Arbeitnow ────────────────────────────────────────────────────────

def gather_arbeitnow(cfg: dict[str, Any], logger) -> list[JobLink]:
    endpoint = cfg.get("endpoint", "https://www.arbeitnow.com/api/job-board-api")
    max_age = cfg.get("max_age_hours", 24)
    out: list[JobLink] = []
    with make_shared_client() as client:
        for q in cfg.get("queries", []):
            params = {"search": q.get("search", "")}
            if q.get("remote"):
                params["remote"] = "true"
            try:
                data = client.get(endpoint, params=params).json().get("data", [])
            except Exception as exc:
                logger.warning("arbeitnow query %s failed: %s", q, exc)
                continue
            for j in data:
                iso = epoch_to_iso(j.get("created_at"))
                if not is_within_hours(iso, max_age):
                    continue
                if q.get("remote") and not j.get("remote"):
                    continue
                url = j.get("url")
                if not url:
                    continue
                out.append(JobLink(
                    url=url, title=j.get("title", ""), company=j.get("company_name", ""),
                    location=j.get("location", ""), source="arbeitnow", date_posted=iso,
                    snippet=j.get("description", ""),
                    # remote=false means "not fully remote" — could be hybrid, so unknown
                    # (False would make the workplace filter treat it as on-site).
                    is_remote=(True if j.get("remote") else None),
                ))
    return out


# ── Himalayas ────────────────────────────────────────────────────────

def gather_himalayas(cfg: dict[str, Any], logger) -> list[JobLink]:
    endpoint = cfg.get("endpoint", "https://himalayas.app/jobs/api")
    max_age = cfg.get("max_age_hours", 24)
    out: list[JobLink] = []
    with make_shared_client() as client:
        for q in cfg.get("queries", []):
            params: dict[str, Any] = {"limit": cfg.get("limit", 100)}
            if q.get("query"):
                params["query"] = q["query"]
            if q.get("seniority"):
                params["seniority"] = q["seniority"]
            try:
                jobs = client.get(endpoint, params=params).json().get("jobs", [])
            except Exception as exc:
                logger.warning("himalayas query %s failed: %s", q, exc)
                continue
            toks = _tokens(q.get("query", ""))
            for j in jobs:
                iso = epoch_to_iso(j.get("pubDate"))
                if not is_within_hours(iso, max_age):
                    continue
                # ?query= isn't always honoured server-side — enforce client-side too
                # (lenient: keep if any significant query word appears).
                if toks and not _keyword_hit(f"{j.get('title','')} {j.get('excerpt','')}", toks):
                    continue
                url = j.get("applicationLink") or j.get("guid")
                if not url:
                    continue
                loc = j.get("locationRestrictions") or []
                # Use the full description (HTML-stripped), not the short excerpt.
                desc = re.sub(r"<[^>]+>", " ", j.get("description") or "").strip() or j.get("excerpt", "")
                out.append(JobLink(
                    url=url, title=j.get("title", ""), company=j.get("companyName", ""),
                    location=", ".join(loc) if isinstance(loc, list) else str(loc),
                    source="himalayas", date_posted=iso, snippet=desc,
                    is_remote=True,
                ))
    return out


# ── JustJoin.it (v2 cursor API) ──────────────────────────────────────

def gather_justjoinit(cfg: dict[str, Any], logger) -> list[JobLink]:
    endpoint = cfg.get("endpoint", "https://api.justjoin.it/v2/user-panel/offers/by-cursor")
    max_age = cfg.get("max_age_hours", 24)
    keywords = cfg.get("keywords", [])
    params = {"orderBy": "DESC", "sortBy": "published", "perPage": cfg.get("per_page", 100)}
    out: list[JobLink] = []
    try:
        with make_shared_client() as client:
            data = client.get(endpoint, params=params).json().get("data", [])
    except Exception as exc:
        logger.warning("justjoinit fetch failed: %s", exc)
        return out
    for j in data:
        posted = j.get("publishedAt") or j.get("lastPublishedAt")
        if not is_within_hours(posted, max_age):
            continue
        skills = " ".join(s.get("name", "") for s in (j.get("requiredSkills") or []) if isinstance(s, dict))
        haystack = f"{j.get('title','')} {skills}"
        if keywords and not _keyword_hit(haystack, keywords):
            continue
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
    """NoFluffJobs criteria search.

    Probed 2026-07-13: the old `rawSearch` body key is IGNORED by the API (identical
    totalCount for any keyword) and the `region` URL param does nothing (hu == pl byte-
    identical) — keyword filtering only works through `criteriaSearch.requirement`, and
    geography only through `criteriaSearch.city`. Config keys: `languages` (jobLanguage
    list, default en+pl), `cities` (optional — e.g. the HU city list for the
    nofluffjobs_hu block), `source_label` (source name on links, default nofluffjobs).
    """
    endpoint = cfg.get("endpoint", "https://nofluffjobs.com/api/search/posting")
    max_age = cfg.get("max_age_hours", 24)
    page_size = cfg.get("page_size", 100)
    languages = cfg.get("languages", ["en", "pl"])
    cities = cfg.get("cities") or []
    label = cfg.get("source_label", "nofluffjobs")
    url_q = {"pageTo": 1, "pageSize": page_size,
             "salaryCurrency": cfg.get("currency", "PLN"), "salaryPeriod": "month"}
    out: list[JobLink] = []
    with make_shared_client(headers={**_UA, "Content-Type": "application/json"}, follow_redirects=True) as client:
        for q in cfg.get("queries", []):
            crit: dict[str, Any] = {"jobLanguage": languages}
            if q.get("keyword"):
                crit["requirement"] = [q["keyword"]]
            if cities:
                crit["city"] = [c.lower() for c in cities]
            body = {"criteriaSearch": crit, "page": 1}
            try:
                postings = client.post(endpoint, params=url_q, json=body).json().get("postings", [])
            except Exception as exc:
                logger.warning("%s query %s failed: %s", label, q, exc)
                continue
            for j in postings:
                iso = epoch_to_iso(j.get("posted"))
                if not is_within_hours(iso, max_age):
                    continue
                # NFJ's fullyRemote flag is unreliable (Poland-centric board), so we
                # record it but don't hard-filter — the scoring phase judges location.
                slug = j.get("url")
                if not slug:
                    continue
                loc = j.get("location") or {}
                places = loc.get("places", []) if isinstance(loc, dict) else []
                city = places[0].get("city", "") if places and isinstance(places[0], dict) else ""
                # e.g. ", Hungary" on the HU block — keeps explicit-onsite jobs in Debrecen/
                # Szeged/etc. inside the ONSITE_LOCATIONS ("hungary") allowance.
                if city and cfg.get("location_suffix"):
                    city += cfg["location_suffix"]
                out.append(JobLink(
                    url=f"https://nofluffjobs.com/job/{slug}", title=j.get("title", ""),
                    company=j.get("name", ""), location=city, source=label,
                    date_posted=iso, snippet=j.get("title", ""),
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


# ── Remote OK ─────────────────────────────────────────────────────────

def gather_remoteok(cfg: dict[str, Any], logger) -> list[JobLink]:
    """RemoteOK public JSON API — free, no auth, optional tag filter.

    Strategy: one request per configured tag (query param ?tag=); short sleep
    between calls to respect their rate limit. Client-side 24h date filter on
    the job["date"] ISO field (job["epoch"] unix-seconds as fallback).
    First element of the response array is always a legal notice object, not a job.
    """
    endpoint = cfg.get("endpoint", "https://remoteok.io/api")
    max_age = cfg.get("max_age_hours", 24)
    tags = cfg.get("tags", [])
    out: list[JobLink] = []
    seen: set[str] = set()
    # RemoteOK blocks the generic Mozilla UA; a slightly more specific one works.
    ua = {"User-Agent": "Mozilla/5.0 (compatible; job-search-bot/1.0)", "Accept": "application/json"}
    queries: list[dict] = [{"tag": t} for t in tags] if tags else [{}]
    with make_shared_client(headers=ua, follow_redirects=True) as client:
        for params in queries:
            try:
                data = client.get(endpoint, params=params or None).json()
            except Exception as exc:
                logger.warning("remoteok query %s failed: %s", params, exc)
                continue
            if not isinstance(data, list):
                continue
            for j in data:
                if not isinstance(j, dict) or not j.get("id"):
                    continue  # skip the legal-notice first element
                url = j.get("url") or f"https://remoteok.io/remote-jobs/{j['id']}"
                if url in seen:
                    continue
                seen.add(url)
                date_str = j.get("date")
                if not date_str and j.get("epoch"):
                    try:
                        date_str = epoch_to_iso(int(j["epoch"]))
                    except Exception:
                        pass
                if not is_within_hours(date_str, max_age):
                    continue
                tags_list = j.get("tags") or []
                out.append(JobLink(
                    url=url,
                    title=j.get("position", ""),
                    company=j.get("company", ""),
                    location=j.get("location") or "Worldwide",
                    source="remoteok",
                    date_posted=date_str,
                    snippet=" | ".join(str(t) for t in tags_list),
                    is_remote=True,
                ))
    return out


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

def _gather_rss(cfg: dict[str, Any], logger, source: str,
                parse_title) -> list[JobLink]:
    """Fetch each configured feed, filter by pubDate recency + keywords, dedup by link.
    parse_title(raw_title) -> (job_title, company, location)."""
    max_age = cfg.get("max_age_hours", 24)
    keywords: list[str] = cfg.get("keywords", [])
    out: list[JobLink] = []
    seen: set[str] = set()
    ua = {"User-Agent": "Mozilla/5.0 (compatible; job-search-bot/1.0)",
          "Accept": "application/rss+xml, text/xml, application/xml"}
    with make_shared_client(headers=ua, follow_redirects=True) as client:
        for feed_url in cfg.get("feeds", []):
            try:
                resp = client.get(feed_url)
            except Exception as exc:
                logger.warning("%s feed %s failed: %s", source, feed_url, exc)
                continue
            root = _rss_root(resp, feed_url, logger, source)
            if root is None:
                continue
            for item in root.findall(".//item"):
                raw_title = (item.findtext("title") or "").strip()
                # <link> is sometimes an empty tag with the URL as a tail text node —
                # ElementTree then sees no text; fall back to <guid>.
                link = (item.findtext("link") or item.findtext("guid") or "").strip()
                if not link or link in seen:
                    continue
                pub_date = _rss_date_to_iso(item.findtext("pubDate"))
                if not is_within_hours(pub_date, max_age):
                    continue
                desc = re.sub(r"<[^>]+>", " ", item.findtext("description") or "").strip()
                if keywords and not _keyword_hit(f"{raw_title} {desc}".lower(), keywords):
                    continue
                seen.add(link)
                job_title, company, location = parse_title(raw_title)
                out.append(JobLink(
                    url=link,
                    title=job_title or raw_title,
                    company=company,
                    location=location or "Remote",
                    source=source,
                    date_posted=pub_date,
                    snippet=desc[:400] if desc else "",
                    is_remote=True,
                ))
    return out


def gather_weworkremotely(cfg: dict[str, Any], logger) -> list[JobLink]:
    """WeWorkRemotely RSS — title format "CompanyName: Job Title [Location, Remote]"."""
    def parse(raw_title: str) -> tuple[str, str, str]:
        if ": " not in raw_title:
            return raw_title, "", "Remote"
        company, _, rest = raw_title.partition(": ")
        if "[" in rest:
            return rest[:rest.rfind("[")].strip(), company, rest[rest.rfind("[") + 1:].rstrip("]").strip()
        return rest.strip(), company, "Remote"
    return _gather_rss(cfg, logger, "weworkremotely", parse)


def gather_workingnomads(cfg: dict[str, Any], logger) -> list[JobLink]:
    """Working Nomads public JSON API (/api/exposed_jobs/) — all-remote board.

    Reprobed 2026-07-23: the old RSS feeds (`?category=...&format=rss`) now 301-redirect to HTML
    and no longer parse. `GET /api/exposed_jobs/` returns the full current listing (~44 jobs) as a
    JSON array — no auth. Field map: `url` (direct), `title`, `company_name`, `description` (HTML),
    `tags` (comma string), `location` (free text, e.g. "REMOTE, Canada"/"Germany"/"Global"),
    `pub_date` ISO-8601 with tz offset. Keyword-filtered client-side on title+description+tags;
    recency on pub_date. is_remote=True (remote-only board)."""
    endpoint = cfg.get("endpoint", "https://www.workingnomads.com/api/exposed_jobs/")
    max_age = cfg.get("max_age_hours", 24)
    kws = [k.lower() for k in cfg.get("keywords", [])]
    out: list[JobLink] = []
    try:
        with make_shared_client() as client:
            data = client.get(endpoint).json()
    except Exception as exc:
        logger.warning("workingnomads fetch failed: %s", exc)
        return out
    for j in (data if isinstance(data, list) else []):
        iso = j.get("pub_date")
        if not is_within_hours(iso, max_age):
            continue
        title = (j.get("title") or "").strip()
        desc = _striptags(j.get("description"))
        hay = f"{title} {desc} {j.get('tags', '')}".lower()
        if kws and not any(k in hay for k in kws):
            continue
        url = (j.get("url") or "").strip()
        if not url:
            continue
        out.append(JobLink(
            url=url, title=title, company=(j.get("company_name") or "").strip(),
            location=(j.get("location") or "Remote").strip(), source="workingnomads",
            date_posted=iso, snippet=desc[:4000], is_remote=True))
    return out


def _striptags(html: str | None) -> str:
    """Collapse an HTML fragment to readable plain text (job descriptions come as HTML)."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()


# ── Jobicy ────────────────────────────────────────────────────────────

def gather_jobicy(cfg: dict[str, Any], logger) -> list[JobLink]:
    """Jobicy public JSON API (jobicy.com/api/v2/remote-jobs) — free, no auth, all remote.

    One GET per configured tag (?tag=<t>&count=<n>); jobs are all remote by definition, so
    scope is enforced on `jobGeo` (many Jobicy roles are region-locked, e.g. 'USA' only) via
    geo_keep. Client-side recency on pubDate (ISO-8601) + keyword filter on title/excerpt.
    """
    endpoint = cfg.get("endpoint", "https://jobicy.com/api/v2/remote-jobs")
    max_age = cfg.get("max_age_hours", 24)
    count = int(cfg.get("count", 50))
    keywords: list[str] = cfg.get("keywords", [])
    tags: list[str] = cfg.get("tags", []) or [""]
    # jobGeo substrings that are in-scope for a UK/EU-based remote candidate. 'anywhere'/
    # 'worldwide' are global-remote; region names cover EU/UK. A US-only remote role is dropped.
    geo_keep = [g.lower() for g in cfg.get("geo_keep", [
        "anywhere", "worldwide", "global", "europe", "emea", "european",
        "united kingdom", "uk", "eu", "hungary"])]
    out: list[JobLink] = []
    seen: set[str] = set()
    with make_shared_client() as client:
        for tag in tags:
            params = {"count": count}
            if tag:
                params["tag"] = tag
            try:
                jobs = client.get(endpoint, params=params).json().get("jobs", [])
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
                geo = (j.get("jobGeo") or "").lower()
                # Keep global-remote and EU/UK; drop region-locked (e.g. USA-only) roles.
                if geo and geo_keep and not any(g in geo for g in geo_keep):
                    continue
                excerpt = _striptags(j.get("jobExcerpt") or j.get("jobDescription"))
                title = j.get("jobTitle", "")
                if keywords and not _keyword_hit(f"{title} {excerpt}".lower(), keywords):
                    continue
                seen.add(url)
                out.append(JobLink(
                    url=url, title=title, company=j.get("companyName", ""),
                    location=j.get("jobGeo") or "Remote", source="jobicy",
                    date_posted=iso, snippet=excerpt[:4000], is_remote=True,
                ))
    return out


# ── The Muse ──────────────────────────────────────────────────────────

def gather_themuse(cfg: dict[str, Any], logger) -> list[JobLink]:
    """The Muse public API (www.themuse.com/api/public/jobs) — free, no auth.

    Requests the 'Flexible / Remote' location across the configured categories/pages. url is
    refs.landing_page; is_remote is set only when a location name actually reads Remote/Flexible
    (a Muse job can carry multiple locations). Client-side recency on publication_date (ISO 'Z').
    """
    endpoint = cfg.get("endpoint", "https://www.themuse.com/api/public/jobs")
    max_age = cfg.get("max_age_hours", 24)
    pages = int(cfg.get("pages", 2))
    categories: list[str] = cfg.get("categories", ["Software Engineering", "Data Science"])
    locations: list[str] = cfg.get("locations", ["Flexible / Remote"])
    keywords: list[str] = cfg.get("keywords", [])
    out: list[JobLink] = []
    seen: set[str] = set()
    with make_shared_client() as client:
        for page in range(1, pages + 1):
            # Repeated category=/location= params -> list of tuples (httpx encodes each).
            params: list[tuple[str, Any]] = [("page", page), ("descending", "true")]
            params += [("category", c) for c in categories]
            params += [("location", loc) for loc in locations]
            try:
                results = client.get(endpoint, params=params).json().get("results", [])
            except Exception as exc:
                logger.warning("themuse page %d failed: %s", page, exc)
                break
            if not results:
                break
            for j in results:
                url = ((j.get("refs") or {}).get("landing_page") or "").strip()
                if not url or url in seen:
                    continue
                iso = j.get("publication_date")
                if not is_within_hours(iso, max_age):
                    continue
                locs = [(l or {}).get("name", "") for l in (j.get("locations") or [])]
                loc_str = ", ".join([l for l in locs if l]) or "Remote"
                remote = any("remote" in l.lower() or "flexible" in l.lower() for l in locs)
                title = j.get("name", "")
                desc = _striptags(j.get("contents"))
                if keywords and not _keyword_hit(f"{title} {desc}".lower(), keywords):
                    continue
                seen.add(url)
                out.append(JobLink(
                    url=url, title=title, company=(j.get("company") or {}).get("name", ""),
                    location=loc_str, source="themuse", date_posted=iso,
                    snippet=desc[:4000], is_remote=(True if remote else None),
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
    searches: list[str] = cfg.get("searches", ["python", "backend", "golang"])
    headers = {**_UA, "Authorization": f"Token {token}"}
    out: list[JobLink] = []
    seen: set[str] = set()
    with make_shared_client(headers=headers, follow_redirects=True) as client:
        for term in searches:
            params = {"search": term, "remote": "true", "sort_by": "date"}
            try:
                results = client.get(endpoint, params=params).json().get("results", [])
            except Exception as exc:
                logger.warning("findwork %r failed: %s", term, exc)
                continue
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
                    date_posted=iso, snippet=_striptags(j.get("text"))[:4000],
                    is_remote=(True if j.get("remote") else None),
                ))
    return out


# ── Adzuna ────────────────────────────────────────────────────────────

def gather_adzuna(cfg: dict[str, Any], logger) -> list[JobLink]:
    """Adzuna aggregator API — broad EU coverage, native recency (max_days_old). Requires FREE
    credentials: set ADZUNA_APP_ID and ADZUNA_APP_KEY in .env. Disabled when either is absent.

    One GET per (country, keyword) with where='remote'; the free tier allows ~250 calls/day, so
    keep countries x keywords modest. is_remote is left None (Adzuna has no exact remote flag —
    where='remote' only biases the query) so the workplace filter/scorer decides."""
    app_id = os.environ.get("ADZUNA_APP_ID") or cfg.get("app_id") or ""
    app_key = os.environ.get("ADZUNA_APP_KEY") or cfg.get("app_key") or ""
    if not (app_id and app_key):
        logger.info("adzuna: ADZUNA_APP_ID/ADZUNA_APP_KEY not set — skipping (free key at developer.adzuna.com)")
        return []
    max_age = cfg.get("max_age_hours", 24)
    per_page = int(cfg.get("results_per_page", 50))
    countries: list[str] = cfg.get("countries", ["gb"])
    keywords: list[str] = cfg.get("keywords", ["python developer", "backend engineer"])
    max_days = max(1, round(max_age / 24))
    out: list[JobLink] = []
    seen: set[str] = set()
    with make_shared_client(headers={"Accept": "application/json"}, follow_redirects=True) as client:
        for country in countries:
            base = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"
            for term in keywords:
                params = {"app_id": app_id, "app_key": app_key, "results_per_page": per_page,
                          "what": term, "where": cfg.get("where", "remote"),
                          "max_days_old": max_days, "sort_by": "date", "content-type": "application/json"}
                try:
                    results = client.get(base, params=params).json().get("results", [])
                except Exception as exc:
                    logger.warning("adzuna %s/%r failed: %s", country, term, exc)
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
                        snippet=_striptags(j.get("description"))[:4000], is_remote=None,
                    ))
    return out


# ── Hacker News "Who is Hiring" ───────────────────────────────────────

_HN_ALGOLIA = "https://hn.algolia.com/api/v1"


def gather_hn_hiring(cfg: dict[str, Any], logger) -> list[JobLink]:
    """Hacker News monthly 'Ask HN: Who is hiring?' thread, via the public Algolia API — unique
    startup/company-direct roles that never hit the job boards. Each top-level comment is one
    posting; the HN convention is a first line like 'Company | Role | Location | REMOTE | stack'.

    We keep only comments that mention remote AND a target keyword, and gate on the comment's
    own created_at (a fresh comment = a fresh posting), so this behaves as a fresh-catcher on the
    same 24h window as everything else. url is the comment permalink (the apply detail is in the
    text — surfaced to the scorer/human)."""
    max_age = cfg.get("max_age_hours", 24)
    keywords: list[str] = cfg.get("keywords", [])
    out: list[JobLink] = []
    with make_shared_client() as client:
        # 1. Find the latest "Who is hiring?" story (exclude "who wants to be hired").
        try:
            hits = client.get(f"{_HN_ALGOLIA}/search_by_date", params={
                "tags": "story,author_whoishiring", "query": "who is hiring",
                "hitsPerPage": 5}).json().get("hits", [])
        except Exception as exc:
            logger.warning("hn_hiring thread search failed: %s", exc)
            return []
        thread_id = next((h.get("objectID") for h in hits
                          if "who is hiring" in (h.get("title") or "").lower()
                          and "hired" not in (h.get("title") or "").lower()), None)
        if not thread_id:
            logger.info("hn_hiring: no current 'Who is hiring' thread found")
            return []
        # 2. Pull its top-level comments (each is a posting).
        try:
            children = client.get(f"{_HN_ALGOLIA}/items/{thread_id}").json().get("children", [])
        except Exception as exc:
            logger.warning("hn_hiring items fetch failed: %s", exc)
            return []
        for c in children:
            text = _striptags(c.get("text"))
            if not text or c.get("author") is None:
                continue
            iso = c.get("created_at")
            if not is_within_hours(iso, max_age):
                continue
            low = text.lower()
            if "remote" not in low:
                continue
            if keywords and not _keyword_hit(low, keywords):
                continue
            # First segment before '|' or newline is usually the company; the next is the role.
            head = re.split(r"\||\n|–|—| - ", text, maxsplit=2)
            company = head[0].strip()[:80] if head else ""
            title = head[1].strip()[:120] if len(head) > 1 else (company or "HN: role")
            out.append(JobLink(
                url=f"https://news.ycombinator.com/item?id={c.get('id')}",
                title=title, company=company, location="Remote", source="hn_hiring",
                date_posted=iso, snippet=text[:4000], is_remote=True,
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
    """Profession.hu keyword RSS feeds — the dominant Hungarian general job board.

    Probed 2026-07-13: `/allasok/1,0,0,<keyword>?rss` returns a keyword-scoped RSS 2.0
    feed of the ~15 newest matching ads (roughly newest-first; the page number in the
    URL is ignored, so there is no pagination — this is a fresh-catcher, which suits
    the 15-min watcher). Field map: <guid> is the clean ad URL (the <link> variant
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
    ua = {"User-Agent": "Mozilla/5.0 (compatible; job-search-bot/1.0)",
          "Accept": "application/rss+xml, text/xml, application/xml"}
    with make_shared_client(headers=ua, follow_redirects=True) as client:
        for kw in cfg.get("keywords", []):
            feed_url = base.format(kw=quote(str(kw)))
            try:
                resp = client.get(feed_url)
            except Exception as exc:
                logger.warning("profession feed %r failed: %s", kw, exc)
                continue
            root = _rss_root(resp, feed_url, logger, "profession")
            if root is None:
                continue
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
                    snippet = " • ".join(str(b) for b in bullets) if isinstance(bullets, list) else desc_raw
                except Exception:
                    snippet = re.sub(r"<[^>]+>", " ", desc_raw).strip()
                out.append(JobLink(
                    url=url, title=title, company="", location=location,
                    source="profession", date_posted=pub_date,
                    snippet=snippet or title,
                    # Explicit remote word only; absence is unknown (most ads are on-site HU,
                    # which the workplace filter allows via ONSITE_LOCATIONS).
                    is_remote=(True if ("remote" in low or "távmunka" in low) else None),
                ))
    return out


# ── DreamJobs.hu (Hungarian IT/startup-focused board) ─────────────────

def gather_dreamjobs(cfg: dict[str, Any], logger) -> list[JobLink]:
    """DreamJobs.hu public JSON API (Nuxt backend), Hungarian IT/startup board.

    Probed 2026-07-13: `GET api.dreamjobs.hu/api/v1/en/jobs?region=hu&scope[]=isNotBlue`
    returns paginated jobs (15/page, `pagination.last_page`; ~74 jobs total on the whole
    board, so polling every page is cheap). Server-side tag filtering 500s, so category
    filtering is client-side on `tags.job_category.slug` (e.g. it-development). Field
    map: `started_at` "YYYY-MM-DD HH:MM:SS" (fromisoformat-compatible), `name`/`slug`
    are per-language dicts, company under `company` (name + slug), job URL is
    `dreamjobs.hu/en/job/<company.slug>/<slug.en>`, locations under
    `tags.office_location.data[].name.en`. No explicit remote flag -> is_remote=None."""
    endpoint = cfg.get("endpoint", "https://api.dreamjobs.hu/api/v1/en/jobs")
    max_age = cfg.get("max_age_hours", 24)
    categories = set(cfg.get("categories", ["it-development", "it-operations-pm"]))
    max_pages = int(cfg.get("max_pages", 5))
    out: list[JobLink] = []
    with make_shared_client() as client:
        page, last_page = 1, 1
        while page <= min(last_page, max_pages):
            params = {"region": cfg.get("region", "hu"), "scope[]": "isNotBlue", "page": page}
            try:
                data = client.get(endpoint, params=params).json()
            except Exception as exc:
                logger.warning("dreamjobs page %d failed: %s", page, exc)
                break
            last_page = int((data.get("pagination") or {}).get("last_page", page))
            for j in data.get("data", []):
                tags = j.get("tags") or {}
                cat = ((tags.get("job_category") or {}).get("slug") or "")
                if categories and cat not in categories:
                    continue
                iso = j.get("started_at")
                if not is_within_hours(iso, max_age):
                    continue
                slug = j.get("slug") or {}
                job_slug = slug.get("en") or slug.get("hu") or ""
                company = j.get("company") or {}
                comp_slug = company.get("slug") or ""
                if not (job_slug and comp_slug):
                    continue
                name = j.get("name") or {}
                title = (name.get("en") or name.get("hu") or "").strip()
                offices = ((tags.get("office_location") or {}).get("data")) or []
                cities = [((o.get("name") or {}).get("en") or "") for o in offices if isinstance(o, dict)]
                joined = ", ".join(c for c in cities if c)
                # Always anchor to Hungary so explicit-onsite jobs outside Budapest still
                # match the ONSITE_LOCATIONS ("hungary") allowance.
                location = f"{joined}, Hungary" if joined else "Hungary"
                out.append(JobLink(
                    url=f"https://dreamjobs.hu/en/job/{comp_slug}/{job_slug}",
                    title=title, company=company.get("name", ""), location=location,
                    source="dreamjobs", date_posted=iso, snippet=title,
                    is_remote=None,  # board has no exact remote flag — scorer/JD fetch decides
                ))
            page += 1
    return out


# ── Jooble (aggregator; hu.jooble.org = strong Hungarian coverage) ────

def gather_jooble(cfg: dict[str, Any], logger) -> list[JobLink]:
    """Jooble partner API — free key required (jooble.org/api/about -> JOOBLE_API_KEY in .env).
    Adapter is a no-op (returns []) while the key is absent, like findwork/adzuna.

    ⚠ NOT yet live-probed (NEW_SOURCE_CHECKLIST §1 needs a key): built from the published
    partner-API contract — POST https://<host>/api/<key> with {"keywords", "location", "page"}
    returning {"totalCount", "jobs": [{title, location, snippet, salary, source, type, link,
    company, updated, id}]}. Field handling is defensive; on the FIRST run with a real key,
    eyeball 3-5 results per checklist §6 before trusting counts. `updated` is the freshest
    date the API offers (updated != posted — reposts may look fresh; the applicant-glut and
    dedup layers still apply). `salary` is a free-text string; it is prepended to the snippet
    so lib/salary.extract() picks it up on the feed card. Jooble links are redirect pages
    (not company ATS), so the surface-time apply-URL resolution handles cross-source dedup —
    do NOT add 'jooble' to watcher._DIRECT_SOURCES."""
    key = os.environ.get("JOOBLE_API_KEY", "").strip()
    if not key:
        return []
    host = cfg.get("host", "hu.jooble.org")
    max_age = cfg.get("max_age_hours", 24)
    suffix = cfg.get("location_suffix", "")
    out: list[JobLink] = []
    seen: set[str] = set()
    with make_shared_client(headers={**_UA, "Content-Type": "application/json"}, follow_redirects=True) as client:
        for q in cfg.get("queries", []):
            body = {"keywords": q.get("keywords", ""), "location": q.get("location", ""), "page": "1"}
            try:
                jobs = client.post(f"https://{host}/api/{key}", json=body).json().get("jobs", [])
            except Exception as exc:
                logger.warning("jooble query %s failed: %s", q, exc)
                continue
            for j in jobs:
                url = (j.get("link") or "").strip()
                if not url or url in seen:
                    continue
                if not is_within_hours(j.get("updated"), max_age):
                    continue
                seen.add(url)
                title = (j.get("title") or "").strip()
                loc = (j.get("location") or "").strip()
                low = f"{title} {loc} {j.get('type') or ''}".lower()
                if suffix and loc and suffix.strip(", ").lower() not in loc.lower():
                    loc += suffix
                # Salary string (when Jooble carries one) rides in front of the snippet so the
                # downstream salary extraction sees it even before the full JD is fetched.
                sal = (j.get("salary") or "").strip()
                snippet = re.sub(r"<[^>]+>", " ", j.get("snippet") or "").strip() or title
                if sal:
                    snippet = f"Salary: {sal}. {snippet}"
                out.append(JobLink(
                    url=url, title=title, company=(j.get("company") or "").strip(),
                    location=loc or (suffix.strip(", ") if suffix else ""),
                    source="jooble", date_posted=j.get("updated"), snippet=snippet,
                    # Tri-state: explicit remote words only; anything else is unknown.
                    is_remote=(True if ("remote" in low or "távmunka" in low or "home office" in low)
                               else None),
                ))
    return out
