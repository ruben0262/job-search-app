"""Direct company ATS job boards — real company postings, not aggregators.

Each ATS hosts one public JSON board per company. There is no global search, so we poll a token
list = config `companies` (seed) UNION tokens HARVESTED for free from two feeds:
  1. the hiring.cafe stream (each hit carries `source` + `board_token`), and
  2. the apply-URL of EVERY job we scrape from EVERY source (LinkedIn/Indeed/RemoteOK/… apply links
     are very often ATS URLs like boards.greenhouse.io/<token>/… — see `harvest_from_urls`).
So the company list self-populates and grows from the whole pipeline, not just one board.
`pipeline/seed_tokens.py` can additionally bulk-seed from public curated company lists.

Supported ATS: Greenhouse, Ashby, Lever, SmartRecruiters, Recruitee, Breezy (verified 2026-07-07),
plus Workday, Personio, Workable (verified 2026-07-08) — all with clean, unauthenticated public
JSON/XML APIs (see docs/GREENHOUSE_SCALE_PLAN.md).
  - Workday tokens are composite `tenant:wdN:site` (e.g. nvidia:wd5:nvidiaexternalcareersite):
    the cxs endpoint needs all three, and they're all in every myworkdayjobs.com job URL. The
    listing is newest-first; postedOn is day-granularity text ("Posted Today", "Posted 2 Days
    Ago") which we convert to a date. Some tenants gate the API (401/403) -> treated as dead.
  - Personio serves an XML feed (ETag-capable) at <token>.jobs.personio.de/xml (.com mirrors .de).
  - Workable is the v1 widget JSON at apply.workable.com/api/v1/widget/accounts/<token>
    (the v3 endpoint that 404'd earlier is POST-only; v1 is simpler and public, no ETag).

Speed for the 15-20 min watcher cycle: boards are polled concurrently, and we send a conditional
`If-None-Match` on every request. Boards that support ETag (Greenhouse, Lever, SmartRecruiters)
answer `304 Not Modified` in a few bytes when nothing changed since we last saw them — which is the
common case between two 15-min polls — so re-polling hundreds of boards costs almost nothing. A 304
is byte-identical board content, i.e. no new jobs, and the pipeline shares one dedup store, so
skipping it loses nothing. ETag is opportunistic: ATS without it (Ashby/Recruitee) just fetch fully.

--- ported from the reference instance, 2026-07-27 ---

Lifted from `job-search/pipeline/sources/ats.py` by
`services/harvester/sources/port_from_reference.py`. Parsing, pagination and per-source quirks are
unchanged on purpose — they are the tuned part, and a faithful copy stays diffable against the
original.

The one systematic change: this module no longer constructs `httpx.Client` itself. Every client
comes from `services.harvester.fetch`, which is what keeps "whose credential paid for this fetch"
answerable, and what stops an ambient `HTTP_PROXY` silently routing the shared harvest through one
user's proxy.
"""
from __future__ import annotations

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
from services.harvester.fetch import make_shared_client
from services.harvester.sources.base import JobLink

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"}
_TIMEOUT = 15
# The three JSON files and their lock are gone. Tokens and ETags live in Postgres via
# `AtsRepository`, injected per call rather than reached for through module state — see
# `libs/corpus/ats.py` for why the file lock could not survive multiple writers.

# hiring.cafe source strings -> our ATS name (greenhouse shows as 'grnhse', lever splits eu/us).
_HARVEST_MAP = {"grnhse": "greenhouse", "greenhouse": "greenhouse", "ashby": "ashby",
                "lever": "lever", "eu_lever": "lever", "smartrecruiters": "smartrecruiters",
                "recruitee": "recruitee", "breezy": "breezy"}

# apply-URL patterns -> (ats, pattern, compose). Tokens are lowercased for storage; these ATS
# APIs are case-insensitive on the token (Workday's site segment verified too), so that's safe
# and keeps the harvested list deduped. compose=None means "token = group(1)"; Workday needs
# tenant+wdN+site composed into one token because its API URL requires all three.
_URL_PATTERNS: list[tuple[str, re.Pattern[str], "Callable[[re.Match[str]], str] | None"]] = [
    ("greenhouse",      re.compile(r"(?:job-boards|boards)\.greenhouse\.io/([A-Za-z0-9_-]+)"), None),
    ("greenhouse",      re.compile(r"boards\.greenhouse\.io/embed/job_app\?for=([A-Za-z0-9_-]+)"), None),
    ("lever",           re.compile(r"jobs\.(?:eu\.)?lever\.co/([A-Za-z0-9_-]+)"), None),
    ("ashby",           re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_-]+)"), None),
    ("smartrecruiters", re.compile(r"jobs\.smartrecruiters\.com/([A-Za-z0-9_-]+)"), None),
    ("recruitee",       re.compile(r"([A-Za-z0-9-]+)\.recruitee\.com"), None),
    ("breezy",          re.compile(r"([A-Za-z0-9-]+)\.breezy\.hr"), None),
    ("workday",         re.compile(r"([A-Za-z0-9_-]+)\.(wd\d+)\.myworkdayjobs\.com/"
                                   r"(?:[a-z]{2}-[A-Z]{2}/)?([^/?#]+)/(?:job|details)/"),
                        lambda m: f"{m.group(1)}:{m.group(2)}:{m.group(3)}"),
    ("personio",        re.compile(r"([A-Za-z0-9-]+)\.jobs\.personio\.(?:de|com)"), None),
    ("workable",        re.compile(r"apply\.workable\.com/([A-Za-z0-9-]+)"), None),
    ("workable",        re.compile(r"([A-Za-z0-9-]+)\.workable\.com"), None),
]
# path/host segments that are NOT company tokens (generic ATS routes and shared hosts).
_NOT_A_TOKEN = {"embed", "job_app", "jobs", "job", "o", "p", "v1", "api", "j", "apply", "www"}


def extract_tokens(text: str) -> list[tuple[str, str]]:
    """All (ats, token) pairs found in a URL or any blob of text containing URLs.
    Shared by the live apply-URL harvest and the bulk seed importer."""
    pairs: list[tuple[str, str]] = []
    for ats, pat, compose in _URL_PATTERNS:
        for m in pat.finditer(text):
            tok = (compose(m) if compose else m.group(1)).lower()
            if tok and tok.split(":", 1)[0] not in _NOT_A_TOKEN:
                pairs.append((ats, tok))
    return pairs

# One pooled client shared by every board poll. Greenhouse/Lever/Ashby/SmartRecruiters serve ALL
# companies from a single API host, so keep-alive reuse skips a TCP+TLS handshake per company —
# the dominant per-request cost when polling hundreds of boards. httpx.Client is thread-safe;
# the default 5s keepalive_expiry drops idle sockets between watcher cycles automatically.
_CLIENT: httpx.Client | None = None
_CLIENT_LOCK = threading.Lock()


def _http() -> httpx.Client:
    global _CLIENT
    if _CLIENT is None:
        with _CLIENT_LOCK:
            if _CLIENT is None:
                _CLIENT = make_shared_client(
                    headers=_UA, timeout=_TIMEOUT, follow_redirects=True,
                    limits=httpx.Limits(max_connections=60, max_keepalive_connections=30))
    return _CLIENT


# ── token harvesting ─────────────────────────────────────────────────────
# Lowercasing and dedup moved into AtsRepository.add_tokens: normalising at the storage boundary
# means every caller gets it, rather than each one remembering to. The counting semantics are
# unchanged - the return value is still a true count of NEW companies, which is what makes the
# "harvested N new tokens" log line meaningful.
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


def harvest_from_urls(repo: AtsRepository, urls: list[str]) -> int:
    """Record ATS tokens found in apply-URLs from ANY source. Every LinkedIn/Indeed/board job whose
    apply link points at a known ATS domain contributes a company token for free. # newly added."""
    pairs = []
    for url in urls:
        if url:
            pairs.extend(extract_tokens(url))
    return _add_tokens(repo, pairs)


# ── ETag cache (conditional polling) ─────────────────────────────────────
# The reference guarded a whole-map JSON rewrite with a file spinlock whose acquire returned False
# on timeout and let the caller proceed anyway - so a contended write silently dropped another
# writer's keys. Row-level upserts remove the problem rather than guarding it, and there is nothing
# left to lock. See `libs/corpus/ats.py`.
def _load_etags(repo: AtsRepository, ats: str) -> dict[str, str]:
    return repo.etags(ats)


# ── generic gather: poll all companies, filter, dedup ────────────────────
def _match(text: str, needles: list[str]) -> bool:
    low = (text or "").lower()
    return any(n in low for n in needles)


# Location strings that carry no usable location ("5 Locations", "Multiple Locations", empty —
# Workday aggregates multi-site postings like this). Fail OPEN on them: the pre-filter can't
# tell UK-included from US-only, so let the scorer judge (same policy as dates/language).
_AGGREGATE_LOC = re.compile(r"^\s*(\d+\s+locations?|multiple locations?)?\s*$", re.I)


class JobFilter:
    """Title/exclude/location/recency relevance pre-filter built from a source's config block.
    Shared by every direct-ATS source and the VC-board adapters (same config anchor keys)."""

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
        # Location passes on: keyword match; a remote-flagged job when 'remote' is accepted
        # (boards often flag remote:true with only a country string); aggregate/unknown text.
        if self.loc_kw and not _match(jl.location, self.loc_kw) \
                and not (jl.is_remote and "remote" in self.loc_kw) \
                and not _AGGREGATE_LOC.match(jl.location or ""):
            return False
        return is_within_hours(jl.date_posted, self.max_age)


def gather_ats(cfg: dict[str, Any], logger, *, ats: str, repo: AtsRepository,
               fetch_company: Callable[[str, str | None], tuple[list[JobLink] | None, str | None]]
               ) -> list[JobLink]:
    """Poll every known company board for one ATS, conditionally where the board supports ETags.

    `repo` is injected rather than reached for through module state. The reference could use a
    module-level store because there was one tenant and one process; here the session belongs to the
    caller's unit of work, and a module-level one would outlive the transaction the results are
    written in.
    """
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
    # Per-board outcomes, applied to the repository after the pool drains. Collected first rather
    # than written inside the worker threads because the SQLAlchemy session is not thread-safe --
    # the fan-out here is deliberately network-bound, and the database work is not part of it.
    outcomes: list[tuple[str, str | None, bool]] = []
    # Per-ATS worker cap: most ATS take 30 concurrent fine, but some (Recruitee) rate-limit a
    # burst from one IP with 429s — set `concurrency` lower in that source's config block.
    workers = int(cfg.get("concurrency", 30))
    with ThreadPoolExecutor(max_workers=max(1, min(len(companies), workers))) as pool:
        for token, jobs, etag in pool.map(_one, companies):
            if use_cond:
                # `jobs == []` means a dead or empty board: forget its validator so a board that
                # comes back is fetched in full rather than compared against an ETag from before it
                # disappeared. `jobs is None` is a 304 and keeps the existing one.
                outcomes.append((token, etag, jobs != []))
            if jobs is None:                      # 304 Not Modified -> nothing new to ingest
                unchanged += 1
                continue
            for jl in jobs:
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
#   jobs is None  -> 304 Not Modified (board unchanged, skip)
#   jobs is []    -> dead token / empty board
def _conditional_get(url: str, etag: str | None) -> tuple[httpx.Response | None, str | None, str]:
    """Returns (response|None, new_etag, status). status in {'ok','304','dead'}."""
    r = _http().get(url, headers={"if-none-match": etag} if etag else None)
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
                           company=token.replace("-", " ").title(), location=loc, source="ashby",
                           date_posted=j.get("publishedAt"),
                           snippet=(j.get("descriptionPlain") or j.get("title") or "")[:2000],
                           is_remote=(True if (j.get("isRemote") or "remote" in loc.lower()) else None)))
    return out, new_etag


def fetch_lever(token: str, etag: str | None = None) -> tuple[list[JobLink] | None, str | None]:
    r, new_etag, status = _conditional_get(f"https://api.lever.co/v0/postings/{token}?mode=json", etag)
    if status != "ok":
        return (None if status == "304" else []), new_etag
    out = []
    for j in (r.json() or []):
        url = (j.get("hostedUrl") or j.get("applyUrl") or "").strip()
        if not url:
            continue
        loc = ((j.get("categories") or {}).get("location") or "").strip()
        out.append(JobLink(url=url, title=(j.get("text") or "").strip(),
                           company=token.replace("-", " ").title(), location=loc, source="lever",
                           date_posted=epoch_to_iso(j.get("createdAt")),
                           snippet=(j.get("descriptionPlain") or j.get("text") or "")[:2000],
                           is_remote=(True if "remote" in loc.lower() else None)))
    return out, new_etag


def fetch_smartrecruiters(token: str, etag: str | None = None) -> tuple[list[JobLink] | None, str | None]:
    r, new_etag, status = _conditional_get(
        f"https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=100", etag)
    if status != "ok":
        return (None if status == "304" else []), new_etag
    out = []
    for j in (r.json().get("content") or []):
        ident = ((j.get("company") or {}).get("identifier") or token).strip()
        jid = (j.get("id") or "").strip()
        if not jid:
            continue
        loc = j.get("location") or {}
        loc_str = ", ".join(x for x in [loc.get("city"), (loc.get("country") or "").upper()] if x)
        out.append(JobLink(url=f"https://jobs.smartrecruiters.com/{ident}/{jid}",
                           title=(j.get("name") or "").strip(), company=ident, location=loc_str,
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
                           snippet=(j.get("description") or j.get("title") or "")[:2000],
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


# 20/page; the listing is newest-first, so we stop early once a page goes stale. The operator's
# value arrives as `max_pages` in the config and is bound onto the fetcher by `gather_workday` —
# this is the fallback for a direct call (token validation in `seed_tokens`) that has no config.
_WD_PAGES = 5
_WD_STALE_DAYS = 4   # comfortably covers the posted_within_hours: 72 window


def _wd_date(posted_on: str | None) -> str | None:
    """Workday's postedOn is day-granularity text: 'Posted Today' / 'Posted Yesterday' /
    'Posted 2 Days Ago' / 'Posted 30+ Days Ago'. Convert to an ISO date (midnight UTC)."""
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
    """token = 'tenant:wdN:site' (all three live in every myworkdayjobs.com job URL).
    POST endpoint, no ETag support; gated tenants (401/403) are treated as dead like a 404."""
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
        # jobDescriptions/*/value carries CDATA HTML paragraphs — strip tags for the snippet.
        desc = re.sub(r"<[^>]+>", " ", " ".join((v.text or "") for v in pos.iter("value"))).strip()
        hay = f"{loc} {pos.findtext('schedule') or ''}".lower()
        out.append(JobLink(url=f"https://{token}.jobs.personio.de/job/{pid}", title=title,
                           company=(pos.findtext("subcompany") or "").strip() or token,
                           location=loc, source="personio",
                           date_posted=(pos.findtext("createdAt") or "").strip() or None,
                           snippet=f"{title}\n{desc}"[:2000],
                           is_remote=(True if "remote" in hay else None)))
    return out, new_etag


def fetch_workable(token: str, etag: str | None = None) -> tuple[list[JobLink] | None, str | None]:
    """v1 widget JSON — public, verified 2026-07-08 (the v3 path that 404'd before is POST-only).
    No ETag served; the conditional plumbing just no-ops."""
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
    # **Bound rather than threaded.** `fetch_company` is one signature shared by nine fetchers, and
    # widening it so that one of them can read a page count would change all nine for the sake of
    # Workday. `partial` keeps the contract and puts the operator's number where it is used.
    fetch = partial(fetch_workday, pages=int(cfg.get("max_pages") or _WD_PAGES))
    return gather_ats(cfg, logger, repo=repo, ats="workday", fetch_company=fetch)


def gather_personio(cfg: dict[str, Any], logger, repo: AtsRepository) -> list[JobLink]:
    return gather_ats(cfg, logger, repo=repo, ats="personio", fetch_company=fetch_personio)


def gather_workable(cfg: dict[str, Any], logger, repo: AtsRepository) -> list[JobLink]:
    return gather_ats(cfg, logger, repo=repo, ats="workable", fetch_company=fetch_workable)


# ats name -> per-company fetcher. Used by gather_* above via gather_ats, and by
# pipeline/seed_tokens.py to validate candidate tokens before adding them.
FETCHERS: dict[str, Callable[[str, str | None], tuple[list[JobLink] | None, str | None]]] = {
    "greenhouse": fetch_greenhouse, "ashby": fetch_ashby, "lever": fetch_lever,
    "smartrecruiters": fetch_smartrecruiters, "recruitee": fetch_recruitee,
    "breezy": fetch_breezy, "workday": fetch_workday, "personio": fetch_personio,
    "workable": fetch_workable,
}
