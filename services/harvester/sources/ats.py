"""Direct company ATS job boards — real company postings, not aggregators."""
from __future__ import annotations

import html
import json
import os
import re
import threading
import unicodedata
import defusedxml.ElementTree as ET   # not stdlib ET: these parse third-party feeds
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from time import monotonic
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

# How many company boards are being read at any moment, across every ATS: as many as the shared client has
# connections. The ATS families are read at the same time (`cycle._gather`), each with its own workers, and
# without this they would outnumber the connections: a read then waits for one on its own clock, and is
# timed out by a board that never heard of it. A worker waits here instead, on no clock, for its turn.
AT_ONCE = 60
_BOARD_TURNS = threading.BoundedSemaphore(AT_ONCE)

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
                    limits=httpx.Limits(max_connections=AT_ONCE, max_keepalive_connections=AT_ONCE // 2))
    return _CLIENT


# ── token harvesting ─────────────────────────────────────────────────────
# Lowercasing and dedup moved into AtsRepository.add_tokens: normalising at the storage boundary.
def load_harvested(repo: AtsRepository, ats: str) -> list[str]:
    return repo.live_tokens(ats)


# ── ETag cache (conditional polling) ──────────────────────────────────────
# Rows in Postgres, not the reference's whole-map JSON rewrite under a spinlock that lost writers.
def _load_etags(repo: AtsRepository, ats: str) -> dict[str, str]:
    return repo.etags(ats)


# What a fetcher answers for a board its ATS says is not there (a 404, a tenant that gates its API): no jobs,
# to a caller that reads only the jobs, and to the poll the one answer that gives a board up. Told from a
# board with nothing open today, which has jobs another day, by being this very list.
GONE: list[JobLink] = []
# What the poll notes for a board that did not answer at all.
_FAILED = object()
# A board not asked this run: the run's share for the company boards was spent before its turn.
_LATER = object()


# ── generic gather: poll the companies, keep what is recent, dedup ────────────────────

def gather_ats(cfg: dict[str, Any], logger, *, ats: str, repo: AtsRepository,
               fetch_company: Callable[[str, str | None], tuple[list[JobLink] | None, str | None]]
               ) -> list[JobLink]:
    """Poll every known company board for one ATS, conditionally where the board supports ETags.

    A board is given up (`AtsRepository.record_poll`) only when its ATS says it is not there (`GONE`). One
    that did not answer (a timeout, a 429, a 5xx, the resolver down) says nothing about itself, and one
    with nothing open today has jobs another day: both are asked again next run. Either used to read as an
    empty board, an empty board as a dead one, and nothing asks a dead board again: 542 of the dev copy's 565
    had gone that way (2026-11-20).

    The boards asked longest ago go first, and none is started after `cfg["until"]`, the company boards' share
    of the run (`cycle.run_cycle`): the rest are the first the next run asks. Every board a crawl lists
    (14,294, 2026-11-23) is more than one run can ask, Workable's 3,664 at two at a time above all."""
    seed = [str(t).strip().lower() for t in (cfg.get("companies") or []) if str(t).strip()]
    companies = list(dict.fromkeys(
        seed + (load_harvested(repo, ats) if cfg.get("use_harvested", True) else [])))
    if cap := int(cfg.get("max_companies", 0)):
        # The `cap` asked longest ago, so every board is polled every few runs. The first `cap` used to be
        # polled every run and the rest never.
        companies = companies[:cap]
    if not companies:
        return []
    max_age = int(cfg.get("posted_within_hours", 0)) or int(cfg.get("max_age_hours", 24))
    use_cond = cfg.get("conditional", True)
    etags = _load_etags(repo, ats) if use_cond else {}
    # For the ATS whose API names no company (Ashby, Lever): the name the board was stored with, or
    # its address written as words.
    names = repo.names(ats)
    until = cfg.get("until")

    def _one(token: str) -> tuple[str, Any, str | None, str]:
        if until is not None and monotonic() >= until:
            return token, _LATER, None, ""
        try:
            with _BOARD_TURNS:
                jobs, etag = fetch_company(token, etags.get(token) if use_cond else None, within=max_age)
            return token, jobs, etag, ""
        except Exception as exc:
            # Why, without the board's own address, so the boards that failed alike are counted together.
            return token, _FAILED, None, re.sub(r"https?://\S+", "<url>", f"{type(exc).__name__}: {exc}")[:90]

    out: list[JobLink] = []
    seen: set[str] = set()
    unchanged = 0
    # Per-board outcomes, applied to the repository after the pool drains.
    outcomes: list[tuple[str, str | None, bool]] = []
    # Per-ATS worker cap: some boards (Recruitee) answer 429 to a burst, so `concurrency` goes lower.
    workers = int(cfg.get("concurrency", 30))
    with ThreadPoolExecutor(max_workers=max(1, min(len(companies), workers))) as pool:
        answers = list(pool.map(_one, companies))
        # A board that did not answer is asked once more, after the rest: of one run's, nearly all were the
        # resolver giving up on a burst of new addresses, and answered a moment later (2026-11-23).
        again = [token for token, jobs, _etag, _why in answers if jobs is _FAILED]
        answers = [answer for answer in answers if answer[1] is not _FAILED] + list(pool.map(_one, again))
    unanswered: Counter[str] = Counter()
    later = [token for token, jobs, _etag, _why in answers if jobs is _LATER]
    repo.record_asked(ats, [token for token, jobs, _etag, _why in answers if jobs is not _LATER])
    for token, jobs, etag, why in answers:
        if jobs is _LATER:
            continue
        if jobs is _FAILED:                       # no answer, twice: nothing learnt about the board
            unanswered[why] += 1
            continue
        if use_cond:
            outcomes.append((token, etag, jobs is not GONE))
        if jobs is None:                          # 304 Not Modified -> nothing new to ingest
            unchanged += 1
            continue
        for jl in jobs:
            jl.company = jl.company or names.get(token) or token.replace("-", " ").title()
            if is_within_hours(jl.date_posted, max_age) and jl.url and jl.url not in seen:
                seen.add(jl.url)
                out.append(jl)
    if use_cond:
        for token, etag, alive in outcomes:
            repo.record_poll(ats, token, etag=etag, alive=alive)
    if unanswered:
        logger.warning("%s: %d of %d boards did not answer twice, and are asked next run: %s", ats,
                       sum(unanswered.values()), len(companies), dict(unanswered.most_common(3)))
    logger.info("%s: polled %d companies (%d unchanged/304, %d asked twice, %d did not answer, %d left for the next "
                "run) -> %d relevant recent jobs", ats, len(companies) - len(later), unchanged, len(again),
                sum(unanswered.values()), len(later), len(out))
    return out

# ── per-ATS fetch: (token, prev_etag) -> (jobs|None, new_etag) ────────────
# None means 304 Not Modified (board unchanged, skip); `GONE` a board that is not there; [] one with nothing
# open; and an answer that is none of those is raised, for the caller to count.
# The most one board's answer is read to. The largest a board needed was 17.9 MB (Ashby's), and Recruitee's
# TransPerfect 11.4 MB; one Recruitee board answered 89.5 MB on each first read, its parse alone most of the
# harvester's 1 GB (2026-11-23). Past it the board is one that did not answer.
_LARGEST_ANSWER = 32_000_000


def _conditional_body(url: str, etag: str | None,
                      timeout: float = _TIMEOUT) -> tuple[bytearray | None, str | None, str]:
    """A board's answer as read, unless it is unchanged (`etag`) or gone: (body|None, new_etag, status), status
    in {'ok','304','dead'}."""
    with _http().stream("GET", url, headers={"if-none-match": etag} if etag else None, timeout=timeout) as r:
        if r.status_code == 304:
            return None, etag, "304"
        if r.status_code == 404:
            return None, None, "dead"
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        body = bytearray()
        for chunk in r.iter_bytes():
            body += chunk
            if len(body) > _LARGEST_ANSWER:
                raise RuntimeError(f"an answer over {_LARGEST_ANSWER // 1_000_000} MB")
        return body, r.headers.get("etag"), "ok"


def _conditional_get(url: str, etag: str | None,
                     timeout: float = _TIMEOUT) -> tuple[httpx.Response | None, str | None, str]:
    """Returns (response|None, new_etag, status). status in {'ok','304','dead'}."""
    body, new_etag, status = _conditional_body(url, etag, timeout)
    return (None if body is None else httpx.Response(200, content=bytes(body))), new_etag, status


def _postings(body: bytearray, marked: str, keep: Callable[[dict], JobLink | None]) -> list[JobLink]:
    """What a board's answer gives the run: each posting made into a job the moment it is read whole, or into
    nothing for one outside the run's window, its texts let go at once. A posting is an object with the field
    `marked`. Parsed whole first, OpenAI's 14 MB on Ashby held about 100 MB while it was read, and every posting
    of it, though a run keeps the few in its window; Ashby's boards, Lever's and Recruitee's took 203, 228 and
    95 MB above the harvester's 67 (2026-11-24)."""
    found: list[JobLink] = []

    def each(value: dict) -> dict | None:
        if marked not in value:
            return value
        if (link := keep(value)) is not None:
            found.append(link)
        return None

    json.loads(body, object_hook=each)
    return found


def _in_window(date: str | None, within: float | None) -> bool:
    """A job a run keeps, asked before anything is made of it: cleaning every offer's text is what made
    Recruitee's 627 boards take 476 s for 172 jobs in the window, and 226 s once only those were cleaned
    (2026-11-23). `within` is the run's window in hours; none (the seed's probe) is every job."""
    return within is None or is_within_hours(date, within)


def fetch_greenhouse(token: str, etag: str | None = None,
                     within: float | None = None) -> tuple[list[JobLink] | None, str | None]:
    """A board's jobs without their text, which a new job's page gives once (the describer). With it
    (`content=true`) the largest boards answer 12 to 17 times as much, SpaceX 33 MB and Anduril 46 MB, and a board
    is sent whole again whenever one of its jobs changes: 28.9 MB for the laptop's 221 boards without (2026-11-23)."""
    body, new_etag, status = _conditional_body(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", etag)
    if status != "ok":
        return (None if status == "304" else GONE), new_etag

    def keep(j: dict) -> JobLink | None:
        url = (j.get("absolute_url") or "").strip()
        loc, date = ((j.get("location") or {}).get("name") or "").strip(), j.get("first_published") or j.get("updated_at")
        if not url or not _in_window(date, within):
            return None
        return JobLink(url=url, title=(j.get("title") or "").strip(),
                       company=(j.get("company_name") or token).strip(), location=loc,
                       source="greenhouse", date_posted=date,
                       snippet=(j.get("title") or ""), is_remote=(True if "remote" in loc.lower() else None))

    return _postings(body, "absolute_url", keep), new_etag


def fetch_ashby(token: str, etag: str | None = None,
                within: float | None = None) -> tuple[list[JobLink] | None, str | None]:
    body, new_etag, status = _conditional_body(f"https://api.ashbyhq.com/posting-api/job-board/{token}", etag)
    if status != "ok":
        return (None if status == "304" else GONE), new_etag

    def keep(j: dict) -> JobLink | None:
        url = (j.get("jobUrl") or j.get("applyUrl") or "").strip()
        if not url or j.get("isListed") is False or not _in_window(j.get("publishedAt"), within):
            return None
        loc = (j.get("location") or "").strip()
        return JobLink(url=url, title=(j.get("title") or "").strip(),
                       company="", location=loc, source="ashby",
                       date_posted=j.get("publishedAt"),
                       # The HTML, whose lists are lists: the plain text makes each item a paragraph.
                       snippet=(strip_html(j.get("descriptionHtml") or "") or j.get("descriptionPlain")
                                or j.get("title") or ""),
                       is_remote=(True if (j.get("isRemote") or "remote" in loc.lower()) else None))

    return _postings(body, "jobUrl", keep), new_etag


def fetch_lever(token: str, etag: str | None = None,
                within: float | None = None) -> tuple[list[JobLink] | None, str | None]:
    body, new_etag, status = _conditional_body(f"https://api.lever.co/v0/postings/{token}?mode=json", etag,
                                               timeout=LEVER_TIMEOUT)
    if status != "ok":
        return (None if status == "304" else GONE), new_etag

    def keep(j: dict) -> JobLink | None:
        url = (j.get("hostedUrl") or j.get("applyUrl") or "").strip()
        if not url or not _in_window(epoch_to_iso(j.get("createdAt")), within):
            return None
        loc = ((j.get("categories") or {}).get("location") or "").strip()
        return JobLink(url=url, title=(j.get("text") or "").strip(),
                       company="", location=loc, source="lever",
                       date_posted=epoch_to_iso(j.get("createdAt")),
                       snippet=_lever_text(j) or j.get("text") or "",
                       is_remote=(True if "remote" in loc.lower() else None))

    return _postings(body, "hostedUrl", keep), new_etag


def _lever_text(posting: dict) -> str:
    """A Lever posting whole: its opening, each of its lists under its heading (what you will do, what
    you bring), and its closing. `descriptionPlain` is the opening alone, which is all that was kept
    until 2026-11-03."""
    lists = "".join(f"<h4>{html.escape(each.get('text') or '')}</h4><ul>{each.get('content') or ''}</ul>"
                    for each in posting.get("lists") or [])
    return strip_html(f"<div>{posting.get('description') or ''}</div>{lists}"
                      f"<div>{posting.get('additional') or ''}</div>")


def fetch_smartrecruiters(token: str, etag: str | None = None,
                          within: float | None = None) -> tuple[list[JobLink] | None, str | None]:
    r, new_etag, status = _conditional_get(
        f"https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=100", etag)
    if status != "ok":
        return (None if status == "304" else GONE), new_etag
    out = []
    for j in (r.json().get("content") or []):
        ident = ((j.get("company") or {}).get("identifier") or token).strip()
        # Its own name ("Bosch Group"), not the identifier in its address ("BoschGroup").
        company = ((j.get("company") or {}).get("name") or ident).strip()
        jid = (j.get("id") or "").strip()
        if not jid or not _in_window(j.get("releasedDate"), within):
            continue
        loc = j.get("location") or {}
        loc_str = ", ".join(x for x in [loc.get("city"), (loc.get("country") or "").upper()] if x)
        out.append(JobLink(url=f"https://jobs.smartrecruiters.com/{ident}/{jid}",
                           title=(j.get("name") or "").strip(), company=company, location=loc_str,
                           source="smartrecruiters", date_posted=j.get("releasedDate"),
                           snippet=(j.get("name") or ""),
                           is_remote=(True if (loc.get("remote") or "remote" in loc_str.lower()) else None)))
    return out, new_etag


def fetch_recruitee(token: str, etag: str | None = None,
                    within: float | None = None) -> tuple[list[JobLink] | None, str | None]:
    body, new_etag, status = _conditional_body(f"https://{token}.recruitee.com/api/offers/", etag)
    if status != "ok":
        return (None if status == "304" else GONE), new_etag

    def keep(j: dict) -> JobLink | None:
        url = (j.get("careers_url") or "").strip()
        # Recruitee dates look like "2026-07-07 12:47:47 UTC" — drop the suffix so the parser accepts it.
        date = (j.get("published_at") or j.get("created_at") or "").replace(" UTC", "").strip() or None
        if not url or not _in_window(date, within):
            return None
        loc = (j.get("location") or ", ".join(x for x in [j.get("city"), j.get("country")] if x)).strip()
        return JobLink(url=url, title=(j.get("title") or "").strip(),
                       company=(j.get("company_name") or token).strip(), location=loc,
                       source="recruitee", date_posted=date,
                       snippet=strip_html(j.get("description") or "") or j.get("title") or "",
                       is_remote=(True if (j.get("remote") or "remote" in loc.lower()) else None))

    return _postings(body, "careers_url", keep), new_etag


def fetch_breezy(token: str, etag: str | None = None,
                 within: float | None = None) -> tuple[list[JobLink] | None, str | None]:
    r, new_etag, status = _conditional_get(f"https://{token}.breezy.hr/json", etag)
    if status != "ok":
        return (None if status == "304" else GONE), new_etag
    out = []
    for j in (r.json() or []):
        url = (j.get("url") or "").strip()
        if not url or not _in_window(j.get("published_date"), within):
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


def fetch_workday(token: str, etag: str | None = None, within: float | None = None, *,
                  pages: int = _WD_PAGES) -> tuple[list[JobLink] | None, str | None]:
    """token = 'tenant:wdN:site' (all three live in every myworkdayjobs.com job URL)."""
    try:
        tenant, wd, site = token.split(":", 2)
    except ValueError:
        return GONE, None    # malformed harvested token -> dead
    api = f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    base = f"https://{tenant}.{wd}.myworkdayjobs.com/{site}"
    out: list[JobLink] = []
    for page in range(pages):
        r = _http().post(api, json={"appliedFacets": {}, "limit": 20,
                                    "offset": page * 20, "searchText": ""})
        # 401/403 = tenant gates the API; 404/422 = tenant or site segment doesn't exist.
        if r.status_code in (401, 403, 404, 422):
            return GONE, None
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
            if not _in_window(date, within):
                stale = True
                continue
            out.append(JobLink(url=base + path, title=title,
                               company=tenant.replace("-", " ").title(),
                               location=(j.get("locationsText") or "").strip(), source="workday",
                               date_posted=date, snippet=title))
        if stale or len(postings) < 20:
            break
    return out, None


def fetch_personio(token: str, etag: str | None = None,
                   within: float | None = None) -> tuple[list[JobLink] | None, str | None]:
    """Public XML feed, ETag-capable. The .de host mirrors .com accounts, so one host serves all."""
    r, new_etag, status = _conditional_get(f"https://{token}.jobs.personio.de/xml", etag)
    if status != "ok":
        return (None if status == "304" else GONE), new_etag
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError:
        return [], new_etag
    out = []
    for pos in root.iter("position"):
        pid = (pos.findtext("id") or "").strip()
        title = (pos.findtext("name") or "").strip()
        date = (pos.findtext("createdAt") or "").strip() or None
        if not pid or not title or not _in_window(date, within):
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
                           date_posted=date, snippet=f"{title}\n{desc}",
                           is_remote=(True if "remote" in hay else None)))
    return out, new_etag


def fetch_workable(token: str, etag: str | None = None,
                   within: float | None = None) -> tuple[list[JobLink] | None, str | None]:
    """v1 widget JSON — public, verified 2026-07-08 (the v3 path that 404'd before is POST-only)."""
    r, new_etag, status = _conditional_get(
        f"https://apply.workable.com/api/v1/widget/accounts/{token}", etag)
    if status != "ok":
        return (None if status == "304" else GONE), new_etag
    d = r.json()
    company = (d.get("name") or token).strip()
    out = []
    for j in (d.get("jobs") or []):
        url = (j.get("url") or j.get("shortlink") or "").strip()
        date = j.get("published_on") or j.get("created_at")
        if not url or not _in_window(date, within):
            continue
        loc = ", ".join(x for x in [j.get("city"), j.get("country")] if x)
        out.append(JobLink(url=url, title=(j.get("title") or "").strip(), company=company,
                           location=loc, source="workable", date_posted=date,
                           snippet=" · ".join(x for x in [j.get("title"), j.get("department")] if x),
                           is_remote=(True if (j.get("telecommuting") or "remote" in loc.lower())
                                      else None)))
    return out, new_etag


# A Teamtailor feed's own fields, and its pages: 100 jobs each, `offset` for the next (Teamtailor's help, "RSS
# feed how-to guide").
_TT = "{https://teamtailor.com/locations}"
_TT_PAGE, _TT_PAGES = 100, 5


def fetch_teamtailor(token: str, etag: str | None = None,
                     within: float | None = None) -> tuple[list[JobLink] | None, str | None]:
    """A company's public feed (`<token>.teamtailor.com/jobs.rss`), which Teamtailor publishes for job boards to
    read, ETag-capable, and a 404 for a company that is not there: each job with its whole text, its time to the
    second and every place it is in, the company as the feed's title names it. Measured 2026-11-23: care homes,
    restaurants and builders in Sweden among the 17 our postings named."""
    r, new_etag, status = _conditional_get(f"https://{token}.teamtailor.com/jobs.rss", etag)
    if status != "ok":
        return (None if status == "304" else GONE), new_etag
    out: list[JobLink] = []
    for page in range(_TT_PAGES):
        if page:
            r = _http().get(f"https://{token}.teamtailor.com/jobs.rss", params={"offset": page * _TT_PAGE},
                            timeout=_TIMEOUT)
            if r.status_code != 200:
                break
        try:
            channel = ET.fromstring(r.content).find("channel")
        except ET.ParseError:
            break
        items = channel.findall("item") if channel is not None else []
        for item in items:
            url, title = (item.findtext("link") or "").strip(), (item.findtext("title") or "").strip()
            date = _rss_time(item.findtext("pubDate"))
            if not url or not title or not _in_window(date, within):
                continue
            places = dict.fromkeys(", ".join(part for part in (
                (place.findtext(f"{_TT}city") or "").strip(), (place.findtext(f"{_TT}country") or "").strip())
                if part) for place in item.iter(f"{_TT}location"))
            out.append(JobLink(url=url, title=title, company=(channel.findtext("title") or token).strip(),
                               location="; ".join(each for each in places if each), source="teamtailor",
                               date_posted=date, snippet=strip_html(item.findtext("description") or ""),
                               is_remote=True if (item.findtext("remoteStatus") or "").strip() == "fully" else None))
        if len(items) < _TT_PAGE:
            break
    return out, new_etag


def _rss_time(text: str | None) -> str | None:
    """An RSS time ("Mon, 23 Nov 2026 13:58:33 +0100") as ISO, or `None` where it gives none."""
    try:
        return parsedate_to_datetime((text or "").strip()).isoformat()
    except (TypeError, ValueError):
        return None


# The ATS whose board is one name, which a company's own name can be tried as: by the seed list
# (`deploy/scripts/seed_ats.py`) and by the names postings carry (`cycle._boards_by_name`). Workday needs a
# tenant, a data centre and a site, so it is found from postings' links only.
FETCHERS: dict[str, Callable[[str], tuple[list[JobLink] | None, str | None]]] = {
    "greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby, "workable": fetch_workable,
    "recruitee": fetch_recruitee, "personio": fetch_personio, "smartrecruiters": fetch_smartrecruiters,
    "breezy": fetch_breezy, "teamtailor": fetch_teamtailor}


def spellings(name: str) -> list[str]:
    """The board names a company's name is tried as: written together and with hyphens ("grafanalabs",
    "grafana-labs"), accents dropped."""
    plain = unicodedata.normalize("NFKD", name.casefold()).encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]+", plain)
    return list(dict.fromkeys(["".join(words), "-".join(words)])) if words else []


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


def gather_teamtailor(cfg: dict[str, Any], logger, repo: AtsRepository) -> list[JobLink]:
    return gather_ats(cfg, logger, repo=repo, ats="teamtailor", fetch_company=fetch_teamtailor)


