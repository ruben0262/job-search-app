"""Direct company ATS job boards — real company postings, not aggregators."""
from __future__ import annotations

import html
import os
import re
import threading
import defusedxml.ElementTree as ET   # not stdlib ET: these parse third-party feeds
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import partial
from typing import Any, Callable

import httpx

from libs.corpus.filters import epoch_to_iso, is_within_hours
from libs.corpus.ats import AtsRepository
from libs.corpus.postings import strip_html
from services.harvester.fetch import make_shared_client
from services.harvester.sources.base import JobLink

_TIMEOUT = 15
# Lever answers a board it has not served lately slowly, whatever its size, and fast after: Palantir
# took 40.9 s, Pigment 25.7 s, Spotify 22 s for its first 50 jobs then 3 s for all 86 (2026-11-02).
# At 15 s every such board failed every pass. Its own limit, so no other board waits this long.
LEVER_TIMEOUT = 60
# The three JSON files and their lock are gone.

# hiring.cafe source strings -> our ATS name (greenhouse shows as 'grnhse', lever splits eu/us).
_HARVEST_MAP = {"grnhse": "greenhouse", "greenhouse": "greenhouse", "ashby": "ashby",
                "lever": "lever", "eu_lever": "lever", "smartrecruiters": "smartrecruiters",
                "recruitee": "recruitee", "breezy": "breezy"}

# One pooled client shared by every board poll.
_CLIENT: httpx.Client | None = None
_CLIENT_LOCK = threading.Lock()


def _http() -> httpx.Client:
    global _CLIENT
    if _CLIENT is None:
        with _CLIENT_LOCK:
            if _CLIENT is None:
                _CLIENT = make_shared_client(
                    timeout=_TIMEOUT, follow_redirects=True,
                    limits=httpx.Limits(max_connections=60, max_keepalive_connections=30))
    return _CLIENT


# ── token harvesting ─────────────────────────────────────────────────────
# Lowercasing and dedup moved into AtsRepository.add_tokens: normalising at the storage boundary.
def load_harvested(repo: AtsRepository, ats: str) -> list[str]:
    return repo.live_tokens(ats)


def _add_tokens(repo: AtsRepository, pairs: list[tuple[str, str]]) -> int:
    return repo.add_tokens(pairs) if pairs else 0


def harvest_from_hits(repo: AtsRepository, hits: list[dict[str, Any]]) -> int:
    """Record ATS board tokens seen in hiring.cafe hits (source + board_token). # newly added."""
    pairs = []
    for h in hits:
        ats = _HARVEST_MAP.get((h.get("source") or "").lower())
        bt = (h.get("board_token") or "").strip()
        if ats and bt:
            pairs.append((ats, bt))
    return _add_tokens(repo, pairs)


# ── ETag cache (conditional polling) ──────────────────────────────────────
# Rows in Postgres, not the reference's whole-map JSON rewrite under a spinlock that lost writers.
def _load_etags(repo: AtsRepository, ats: str) -> dict[str, str]:
    return repo.etags(ats)


# ── generic gather: poll all companies, filter, dedup ────────────────────
def _match(text: str, needles: list[str]) -> bool:
    low = (text or "").lower()
    return any(n in low for n in needles)


# Location strings carrying no usable location: Workday aggregates multi-site postings like this.
_AGGREGATE_LOC = re.compile(r"^\s*(\d+\s+locations?|multiple locations?)?\s*$", re.I)


class JobFilter:
    """Title/exclude/location/recency relevance pre-filter built from a source's config block."""

    def __init__(self, cfg: dict[str, Any]):
        self.keywords = [k.lower() for k in (cfg.get("keywords") or [])]
        self.excludes = [k.lower() for k in (cfg.get("title_exclude") or [])]
        self.loc_kw = [k.lower() for k in (cfg.get("location_keywords") or [])]
        self.max_age = int(cfg.get("posted_within_hours", 0)) or int(cfg.get("max_age_hours", 24))

    def ok(self, jl: JobLink) -> bool:
        if self.keywords and not _match(jl.title, self.keywords):
            return False
        if self.excludes and _match(jl.title, self.excludes):
            return False
        # Location passes on a keyword match, a remote flag when remote is accepted, or an aggregate.
        if self.loc_kw and not _match(jl.location, self.loc_kw) \
                and not (jl.is_remote and "remote" in self.loc_kw) \
                and not _AGGREGATE_LOC.match(jl.location or ""):
            return False
        return is_within_hours(jl.date_posted, self.max_age)


def gather_ats(cfg: dict[str, Any], logger, *, ats: str, repo: AtsRepository,
               fetch_company: Callable[[str, str | None], tuple[list[JobLink] | None, str | None]]
               ) -> list[JobLink]:
    """Poll every known company board for one ATS, conditionally where the board supports ETags."""
    seed = [str(t).strip().lower() for t in (cfg.get("companies") or []) if str(t).strip()]
    companies = list(dict.fromkeys(
        seed + (load_harvested(repo, ats) if cfg.get("use_harvested", True) else [])))
    if cap := int(cfg.get("max_companies", 0)):
        companies = companies[:cap]
    if not companies:
        return []
    jf = JobFilter(cfg)
    use_cond = cfg.get("conditional", True)
    etags = _load_etags(repo, ats) if use_cond else {}
    # For the ATS whose API names no company (Ashby, Lever): the name the board was stored with, or
    # its address written as words.
    names = repo.names(ats)

    def _one(token: str) -> tuple[str, list[JobLink] | None, str | None]:
        try:
            jobs, etag = fetch_company(token, etags.get(token) if use_cond else None)
            return token, jobs, etag
        except Exception as exc:
            logger.warning("%s: %s failed: %s", ats, token, exc)
            return token, [], None

    out: list[JobLink] = []
    seen: set[str] = set()
    unchanged = 0
    # Per-board outcomes, applied to the repository after the pool drains.
    outcomes: list[tuple[str, str | None, bool]] = []
    # Per-ATS worker cap: some boards (Recruitee) answer 429 to a burst, so `concurrency` goes lower.
    workers = int(cfg.get("concurrency", 30))
    with ThreadPoolExecutor(max_workers=max(1, min(len(companies), workers))) as pool:
        for token, jobs, etag in pool.map(_one, companies):
            if use_cond:
                # `jobs == []` is a dead board: forget its validator so it is fetched in full if it returns.
                outcomes.append((token, etag, jobs != []))
            if jobs is None:                      # 304 Not Modified -> nothing new to ingest
                unchanged += 1
                continue
            for jl in jobs:
                jl.company = jl.company or names.get(token) or token.replace("-", " ").title()
                if jf.ok(jl) and jl.url and jl.url not in seen:
                    seen.add(jl.url)
                    out.append(jl)
    if use_cond:
        for token, etag, alive in outcomes:
            repo.record_poll(ats, token, etag=etag, alive=alive)
    logger.info("%s: polled %d companies (%d unchanged/304) -> %d relevant recent jobs",
                ats, len(companies), unchanged, len(out))
    return out

# ── per-ATS fetch: (token, prev_etag) -> (jobs|None, new_etag) ────────────
# None means 304 Not Modified (board unchanged, skip); [] means a dead token or an empty board.
def _conditional_get(url: str, etag: str | None,
                     timeout: float = _TIMEOUT) -> tuple[httpx.Response | None, str | None, str]:
    """Returns (response|None, new_etag, status). status in {'ok','304','dead'}."""
    r = _http().get(url, headers={"if-none-match": etag} if etag else None, timeout=timeout)
    if r.status_code == 304:
        return None, etag, "304"
    if r.status_code == 404:
        return None, None, "dead"
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    return r, r.headers.get("etag"), "ok"


def fetch_greenhouse(token: str, etag: str | None = None) -> tuple[list[JobLink] | None, str | None]:
    r, new_etag, status = _conditional_get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", etag)
    if status != "ok":
        return (None if status == "304" else []), new_etag
    out = []
    for j in (r.json().get("jobs") or []):
        url = (j.get("absolute_url") or "").strip()
        if not url:
            continue
        loc = ((j.get("location") or {}).get("name") or "").strip()
        out.append(JobLink(url=url, title=(j.get("title") or "").strip(),
                           company=(j.get("company_name") or token).strip(), location=loc,
                           source="greenhouse", date_posted=(j.get("first_published") or j.get("updated_at")),
                           snippet=(j.get("title") or ""), is_remote=(True if "remote" in loc.lower() else None)))
    return out, new_etag


def fetch_ashby(token: str, etag: str | None = None) -> tuple[list[JobLink] | None, str | None]:
    r, new_etag, status = _conditional_get(f"https://api.ashbyhq.com/posting-api/job-board/{token}", etag)
    if status != "ok":
        return (None if status == "304" else []), new_etag
    out = []
    for j in (r.json().get("jobs") or []):
        url = (j.get("jobUrl") or j.get("applyUrl") or "").strip()
        if not url or j.get("isListed") is False:
            continue
        loc = (j.get("location") or "").strip()
        out.append(JobLink(url=url, title=(j.get("title") or "").strip(),
                           company="", location=loc, source="ashby",
                           date_posted=j.get("publishedAt"),
                           # The HTML, whose lists are lists: the plain text makes each item a paragraph.
                           snippet=(strip_html(j.get("descriptionHtml") or "") or j.get("descriptionPlain")
                                    or j.get("title") or ""),
                           is_remote=(True if (j.get("isRemote") or "remote" in loc.lower()) else None)))
    return out, new_etag


def fetch_lever(token: str, etag: str | None = None) -> tuple[list[JobLink] | None, str | None]:
    r, new_etag, status = _conditional_get(f"https://api.lever.co/v0/postings/{token}?mode=json", etag,
                                           timeout=LEVER_TIMEOUT)
    if status != "ok":
        return (None if status == "304" else []), new_etag
    out = []
    for j in (r.json() or []):
        url = (j.get("hostedUrl") or j.get("applyUrl") or "").strip()
        if not url:
            continue
        loc = ((j.get("categories") or {}).get("location") or "").strip()
        out.append(JobLink(url=url, title=(j.get("text") or "").strip(),
                           company="", location=loc, source="lever",
                           date_posted=epoch_to_iso(j.get("createdAt")),
                           snippet=_lever_text(j) or j.get("text") or "",
                           is_remote=(True if "remote" in loc.lower() else None)))
    return out, new_etag


def _lever_text(posting: dict) -> str:
    """A Lever posting whole: its opening, each of its lists under its heading (what you will do, what
    you bring), and its closing. `descriptionPlain` is the opening alone, which is all that was kept
    until 2026-11-03."""
    lists = "".join(f"<h4>{html.escape(each.get('text') or '')}</h4><ul>{each.get('content') or ''}</ul>"
                    for each in posting.get("lists") or [])
    return strip_html(f"<div>{posting.get('description') or ''}</div>{lists}"
                      f"<div>{posting.get('additional') or ''}</div>")


def fetch_smartrecruiters(token: str, etag: str | None = None) -> tuple[list[JobLink] | None, str | None]:
    r, new_etag, status = _conditional_get(
        f"https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=100", etag)
    if status != "ok":
        return (None if status == "304" else []), new_etag
    out = []
    for j in (r.json().get("content") or []):
        ident = ((j.get("company") or {}).get("identifier") or token).strip()
        # Its own name ("Bosch Group"), not the identifier in its address ("BoschGroup").
        company = ((j.get("company") or {}).get("name") or ident).strip()
        jid = (j.get("id") or "").strip()
        if not jid:
            continue
        loc = j.get("location") or {}
        loc_str = ", ".join(x for x in [loc.get("city"), (loc.get("country") or "").upper()] if x)
        out.append(JobLink(url=f"https://jobs.smartrecruiters.com/{ident}/{jid}",
                           title=(j.get("name") or "").strip(), company=company, location=loc_str,
                           source="smartrecruiters", date_posted=j.get("releasedDate"),
                           snippet=(j.get("name") or ""),
                           is_remote=(True if (loc.get("remote") or "remote" in loc_str.lower()) else None)))
    return out, new_etag


def fetch_recruitee(token: str, etag: str | None = None) -> tuple[list[JobLink] | None, str | None]:
    r, new_etag, status = _conditional_get(f"https://{token}.recruitee.com/api/offers/", etag)
    if status != "ok":
        return (None if status == "304" else []), new_etag
    out = []
    for j in (r.json().get("offers") or []):
        url = (j.get("careers_url") or "").strip()
        if not url:
            continue
        loc = (j.get("location") or ", ".join(x for x in [j.get("city"), j.get("country")] if x)).strip()
        # Recruitee dates look like "2026-07-07 12:47:47 UTC" — drop the suffix so the parser accepts it.
        date = (j.get("published_at") or j.get("created_at") or "").replace(" UTC", "").strip() or None
        out.append(JobLink(url=url, title=(j.get("title") or "").strip(),
                           company=(j.get("company_name") or token).strip(), location=loc,
                           source="recruitee", date_posted=date,
                           snippet=strip_html(j.get("description") or "") or j.get("title") or "",
                           is_remote=(True if (j.get("remote") or "remote" in loc.lower()) else None)))
    return out, new_etag


def fetch_breezy(token: str, etag: str | None = None) -> tuple[list[JobLink] | None, str | None]:
    r, new_etag, status = _conditional_get(f"https://{token}.breezy.hr/json", etag)
    if status != "ok":
        return (None if status == "304" else []), new_etag
    out = []
    for j in (r.json() or []):
        url = (j.get("url") or "").strip()
        if not url:
            continue
        loc = j.get("location") or {}
        city = loc.get("city") or ""
        country = ((loc.get("country") or {}).get("name") or "") if isinstance(loc.get("country"), dict) else ""
        loc_str = ", ".join(x for x in [city, country] if x)
        out.append(JobLink(url=url, title=(j.get("name") or "").strip(),
                           company=token.replace("-", " ").title(), location=loc_str, source="breezy",
                           date_posted=j.get("published_date"), snippet=(j.get("name") or ""),
                           is_remote=(True if (loc.get("is_remote") or "remote" in loc_str.lower()) else None)))
    return out, new_etag


# 20/page; the listing is newest-first, so we stop early once a page goes stale.
_WD_PAGES = 5
_WD_STALE_DAYS = 4   # comfortably covers the posted_within_hours: 72 window


def _wd_date(posted_on: str | None) -> str | None:
    """Workday's postedOn is day-granularity text: 'Posted Today', 'Posted 2 Days Ago'."""
    s = (posted_on or "").lower()
    if "today" in s:
        days = 0
    elif "yesterday" in s:
        days = 1
    else:
        m = re.search(r"(\d+)\+?\s*days?\s+ago", s)
        if not m:
            return None
        days = int(m.group(1))
    return (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()


def fetch_workday(token: str, etag: str | None = None, *,
                  pages: int = _WD_PAGES) -> tuple[list[JobLink] | None, str | None]:
    """token = 'tenant:wdN:site' (all three live in every myworkdayjobs.com job URL)."""
    try:
        tenant, wd, site = token.split(":", 2)
    except ValueError:
        return [], None    # malformed harvested token -> dead
    api = f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    base = f"https://{tenant}.{wd}.myworkdayjobs.com/{site}"
    today = datetime.now(timezone.utc).date()
    out: list[JobLink] = []
    for page in range(pages):
        r = _http().post(api, json={"appliedFacets": {}, "limit": 20,
                                    "offset": page * 20, "searchText": ""})
        # 401/403 = tenant gates the API; 404/422 = tenant or site segment doesn't exist.
        if r.status_code in (401, 403, 404, 422):
            return [], None
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        postings = r.json().get("jobPostings") or []
        stale = False
        for j in postings:
            path = (j.get("externalPath") or "").strip()
            title = (j.get("title") or "").strip()
            if not path or not title:
                continue
            date = _wd_date(j.get("postedOn"))
            if date and (today - datetime.fromisoformat(date).date()).days > _WD_STALE_DAYS:
                stale = True
                continue
            out.append(JobLink(url=base + path, title=title,
                               company=tenant.replace("-", " ").title(),
                               location=(j.get("locationsText") or "").strip(), source="workday",
                               date_posted=date, snippet=title))
        if stale or len(postings) < 20:
            break
    return out, None


def fetch_personio(token: str, etag: str | None = None) -> tuple[list[JobLink] | None, str | None]:
    """Public XML feed, ETag-capable. The .de host mirrors .com accounts, so one host serves all."""
    r, new_etag, status = _conditional_get(f"https://{token}.jobs.personio.de/xml", etag)
    if status != "ok":
        return (None if status == "304" else []), new_etag
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError:
        return [], new_etag
    out = []
    for pos in root.iter("position"):
        pid = (pos.findtext("id") or "").strip()
        title = (pos.findtext("name") or "").strip()
        if not pid or not title:
            continue
        offices = [pos.findtext("office") or ""] + \
                  [o.text or "" for o in pos.findall("additionalOffices/office")]
        loc = ", ".join(dict.fromkeys(x.strip() for x in offices if x and x.strip()))
        # jobDescriptions/*/value carries CDATA HTML paragraphs, each its own.
        desc = strip_html("".join(f"<div>{v.text or ''}</div>" for v in pos.iter("value")))
        hay = f"{loc} {pos.findtext('schedule') or ''}".lower()
        out.append(JobLink(url=f"https://{token}.jobs.personio.de/job/{pid}", title=title,
                           company=(pos.findtext("subcompany") or "").strip() or token,
                           location=loc, source="personio",
                           date_posted=(pos.findtext("createdAt") or "").strip() or None,
                           snippet=f"{title}\n{desc}",
                           is_remote=(True if "remote" in hay else None)))
    return out, new_etag


def fetch_workable(token: str, etag: str | None = None) -> tuple[list[JobLink] | None, str | None]:
    """v1 widget JSON — public, verified 2026-07-08 (the v3 path that 404'd before is POST-only)."""
    r, new_etag, status = _conditional_get(
        f"https://apply.workable.com/api/v1/widget/accounts/{token}", etag)
    if status != "ok":
        return (None if status == "304" else []), new_etag
    d = r.json()
    company = (d.get("name") or token).strip()
    out = []
    for j in (d.get("jobs") or []):
        url = (j.get("url") or j.get("shortlink") or "").strip()
        if not url:
            continue
        loc = ", ".join(x for x in [j.get("city"), j.get("country")] if x)
        out.append(JobLink(url=url, title=(j.get("title") or "").strip(), company=company,
                           location=loc, source="workable",
                           date_posted=j.get("published_on") or j.get("created_at"),
                           snippet=" · ".join(x for x in [j.get("title"), j.get("department")] if x),
                           is_remote=(True if (j.get("telecommuting") or "remote" in loc.lower())
                                      else None)))
    return out, new_etag


def gather_greenhouse(cfg: dict[str, Any], logger, repo: AtsRepository) -> list[JobLink]:
    return gather_ats(cfg, logger, repo=repo, ats="greenhouse", fetch_company=fetch_greenhouse)


def gather_ashby(cfg: dict[str, Any], logger, repo: AtsRepository) -> list[JobLink]:
    return gather_ats(cfg, logger, repo=repo, ats="ashby", fetch_company=fetch_ashby)


def gather_lever(cfg: dict[str, Any], logger, repo: AtsRepository) -> list[JobLink]:
    return gather_ats(cfg, logger, repo=repo, ats="lever", fetch_company=fetch_lever)


def gather_smartrecruiters(cfg: dict[str, Any], logger, repo: AtsRepository) -> list[JobLink]:
    return gather_ats(cfg, logger, repo=repo, ats="smartrecruiters", fetch_company=fetch_smartrecruiters)


def gather_recruitee(cfg: dict[str, Any], logger, repo: AtsRepository) -> list[JobLink]:
    return gather_ats(cfg, logger, repo=repo, ats="recruitee", fetch_company=fetch_recruitee)


def gather_breezy(cfg: dict[str, Any], logger, repo: AtsRepository) -> list[JobLink]:
    return gather_ats(cfg, logger, repo=repo, ats="breezy", fetch_company=fetch_breezy)


def gather_workday(cfg: dict[str, Any], logger, repo: AtsRepository) -> list[JobLink]:
    # Bound rather than threaded.
    fetch = partial(fetch_workday, pages=int(cfg.get("max_pages") or _WD_PAGES))
    return gather_ats(cfg, logger, repo=repo, ats="workday", fetch_company=fetch)


def gather_personio(cfg: dict[str, Any], logger, repo: AtsRepository) -> list[JobLink]:
    return gather_ats(cfg, logger, repo=repo, ats="personio", fetch_company=fetch_personio)


def gather_workable(cfg: dict[str, Any], logger, repo: AtsRepository) -> list[JobLink]:
    return gather_ats(cfg, logger, repo=repo, ats="workable", fetch_company=fetch_workable)


