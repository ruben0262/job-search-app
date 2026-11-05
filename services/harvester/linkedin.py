"""The LinkedIn pass: every country somebody picked, read whole with no search word on every run, and
every job stored. Words are matched here, on title and text, never at LinkedIn (owner, 2026-11-04): a job
is stored once for everybody, and a word added tomorrow finds today's jobs. Each job's text is read by
`services.harvester.describer`, all the time, not here.

A pass costs a request per ten jobs a place lists, the same however many people or words there are.
Measured 2026-11-04, one 15-minute window with no word: the EU 659 jobs, Spain 111, France 110, Germany
104, the UK 87, Italy 78, the Netherlands 59, Ireland 18.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
from bs4 import BeautifulSoup

from libs.corpus.geo import COUNTRIES, COUNTRY_LABELS
from libs.corpus.postings import LI_BACKOFF, LI_RETRIES
from libs.corpus.repository import CorpusRepository
from libs.db import lease
from libs.db.models import SweptPlace
from libs.obs import audit
from libs.proxy import ENV as PROXY_ENV
from libs.proxy import site_proxy
from libs.secrets import redacting
from libs.settings import HARVEST_CYCLE_BUDGET_SECONDS, PLATFORM_TICK_MIN, freshness_hours, proxy_exits
from libs.tenant.repository import TenantRepository
from services.harvester.fetch import make_site_client
from services.harvester.governor import linkedin_enabled
from services.harvester.source_config import union_locations
from services.harvester.sources.base import JobLink

logger = logging.getLogger(__name__)

# How long a pass may run, sweeping and describing together: the shared cycle's budget, since the two run
# side by side on one tick (`services.harvester.__main__`), and the tick's words come after both.
PASS_BUDGET_SECONDS = HARVEST_CYCLE_BUDGET_SECONDS

# LinkedIn's guest job list: ten jobs a page, and nothing past the thousandth.
SWEEP_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
SWEEP_PAGE, SWEEP_DEPTH = 10, 1000
# LinkedIn answers a refusal with the same empty page as the end of its list: a sweep of Germany's last
# hour stopped at 20 of ~300 jobs that way (2026-11-03). After a full page, an empty one is asked again,
# each time from another of the proxy's addresses, before it counts as the end.
EMPTY_RETRIES = 3


@dataclass
class LinkedInReport:
    """What one pass did."""

    # Places read, the requests that took, and the jobs they listed.
    swept_places: int = 0
    sweep_requests: int = 0
    swept: int = 0
    new_jobs: int = 0
    skipped_locked: bool = False
    failures: dict[str, str] = field(default_factory=dict)


@dataclass
class Sweep:
    """What one sweep of one place read."""

    place: str
    requests: int = 0
    jobs: list[JobLink] = field(default_factory=list)
    # Stopped at LinkedIn's thousandth job: the window held more than one list can be read to.
    truncated: bool = False
    # Stopped on empty pages after a full one, the retries spent: the end, or LinkedIn refusing.
    unsure_end: bool = False
    # Connections the proxy could not open (a 503 from one of its addresses), each asked again.
    refused_connections: int = 0


def wanted_places(configs: dict) -> list[str]:
    """LinkedIn's name for every country somebody picked, each once; a region is read as its countries.
    LinkedIn lists no further than 1,000 jobs, and the EU lists ~660 in 15 minutes (2026-11-04), so the
    30 minutes a sweep reaches back would lose its oldest ~300 every run. Its biggest country lists ~110."""
    codes = {code for slug in union_locations(configs) if slug in COUNTRIES
             for code in COUNTRIES[slug].countries}
    return sorted(COUNTRY_LABELS[code] for code in codes if code in COUNTRY_LABELS)


def sweep_window(swept_at: datetime | None, *, now: datetime, most: int) -> int:
    """How far back, in seconds, a sweep of a place reaches: to its previous sweep and one run before it,
    so a job listed at the edge of one window is inside the next; at most `most` hours, the operator's
    window, which is also how far a place nobody has swept yet reaches. Measured 2026-11-03: a sweep missed
    8 of 20 jobs listed exactly an hour before, and a wider window held all 8."""
    if swept_at is None:
        return most * 3600
    since = (now - swept_at.replace(tzinfo=swept_at.tzinfo or UTC)).total_seconds()
    return int(min(most * 3600, since + PLATFORM_TICK_MIN * 60))


def sweep_places(places: dict[str, int], *, proxy: str, readers: int) -> list[Sweep]:
    """Every job each place listed in its window (seconds), newest first, with no search word: `readers`
    places at once, each read a page after the other."""
    with redacting(proxy), make_site_client(proxy) as client, ThreadPoolExecutor(readers) as pool:
        return list(pool.map(lambda place: _sweep(client, place, places[place]), places))


def _sweep(client, place: str, seconds: int) -> Sweep:
    sweep = Sweep(place)
    params = {"keywords": "", "location": place, "f_TPR": f"r{seconds}", "sortBy": "DD"}
    start = empty = 0
    while start < SWEEP_DEPTH:
        response = _page(client, {**params, "start": start}, sweep)
        cards = _cards(response.text) if response is not None and response.status_code == 200 else []
        if not cards:
            if start and empty < EMPTY_RETRIES:
                empty += 1
                continue
            sweep.unsure_end = bool(start)
            return sweep
        empty = 0
        sweep.jobs += cards
        if len(cards) < SWEEP_PAGE:
            return sweep
        start += SWEEP_PAGE
    sweep.truncated = True
    return sweep


def _page(client, params: dict, sweep: Sweep) -> httpx.Response | None:
    """One page of the list, asked again after a 429 or a connection the proxy could not open, each time
    from the proxy's next address."""
    response = None
    for attempt in range(LI_RETRIES):
        sweep.requests += 1
        try:
            response = client.get(SWEEP_URL, params=params)
        except httpx.HTTPError:
            # The proxy could not open this connection, or it broke: the next try leaves from another
            # address. Taken as an empty page, it ended twelve countries' lists early every run, so each
            # was read again from hours back (2026-11-05). Counted, and said once for the pass.
            sweep.refused_connections += 1
            continue
        if response.status_code != 429:
            return response
        time.sleep(LI_BACKOFF * (attempt + 1))
    return response


def _cards(html: str) -> list[JobLink]:
    """The jobs on one page of the list: title, company, place and day; the text is read after."""
    out: list[JobLink] = []
    for card in BeautifulSoup(html, "html.parser").find_all(class_="base-search-card"):
        job_id = str(card.get("data-entity-urn", "")).rsplit(":", 1)[-1]
        title = card.find("h3", class_="base-search-card__title")
        if not job_id.isdigit() or title is None:
            continue
        company = card.find("h4", class_="base-search-card__subtitle")
        where = card.find("span", class_="job-search-card__location")
        posted = card.find("time")
        out.append(JobLink(url=f"https://www.linkedin.com/jobs/view/{job_id}",
                           title=title.get_text(strip=True),
                           company=company.get_text(strip=True) if company else "",
                           location=where.get_text(strip=True) if where else "", source="linkedin",
                           date_posted=posted.get("datetime") if posted else None, found_via="sweep"))
    return out


def run_pass(db, *, now: datetime | None = None, sweep=None) -> LinkedInReport:
    """One pass: every place somebody picked, swept, and what is new stored. Held as the `linkedin` lease,
    which the describer waits on, so the two never spend LinkedIn's allowance at once. `sweep` is
    `sweep_places`, looked up when called."""
    report = LinkedInReport()
    if not linkedin_enabled():
        logger.warning("linkedin: tier disabled by HARVEST_LINKEDIN_DISABLED")
        return report
    proxy = site_proxy()
    if proxy is None:
        logger.warning("linkedin: %s is not set, so LinkedIn was not read", PROXY_ENV)
        return report

    with lease(db, "linkedin", PASS_BUDGET_SECONDS) as acquired:
        if not acquired:
            # The previous pass is still sweeping: two at once would spend LinkedIn's allowance twice.
            logger.warning("linkedin: the previous pass still holds the lease, skipping this tick")
            report.skipped_locked = True
            return report
        # A page at once from each of the proxy's addresses (`libs.settings.DEFAULT_PROXY_EXITS`).
        _swept(db, CorpusRepository(db), report, now=now or datetime.now(UTC), proxy=proxy,
               readers=proxy_exits(db), sweep=sweep or sweep_places)

    audit(logger, "linkedin.pass", places=report.swept_places, requests=report.sweep_requests,
          swept=report.swept, new=report.new_jobs, failures=len(report.failures))
    return report


def _swept(db, corpus: CorpusRepository, report: LinkedInReport, *, now: datetime, proxy: str, readers: int,
           sweep) -> None:
    """Every place's new jobs, each stored once, and when each place was read whole."""
    places = wanted_places(TenantRepository(db).active_configs())
    if not places:
        logger.warning("linkedin: nobody active has picked a country, so no place was read")
        return
    most = freshness_hours(db, "linkedin")
    last = {row["_id"]: row["swept_at"] for row in db[SweptPlace.COLLECTION].find({"_id": {"$in": places}})}
    try:
        sweeps = sweep({place: sweep_window(last.get(place), now=now, most=most) for place in places},
                       proxy=proxy, readers=readers)
    except Exception as exc:                                        # noqa: BLE001
        # Said, and the pass ends: what earlier passes stored is still read by the describer.
        report.failures["sweep"] = f"{type(exc).__name__}: {exc}"
        logger.warning("linkedin: the sweep failed: %s", type(exc).__name__)
        return

    jobs = [job for each in sweeps for job in each.jobs]
    report.new_jobs = _ingest(corpus, jobs)
    report.swept_places = len(sweeps)
    report.sweep_requests = sum(each.requests for each in sweeps)
    report.swept = len(jobs)
    for each in sweeps:
        # Read to its end: the next sweep reaches back to here. One that ended on refusals reaches further.
        if not each.unsure_end:
            db[SweptPlace.COLLECTION].update_one({"_id": each.place}, {"$set": {"swept_at": now}},
                                                 upsert=True)
    logger.info("linkedin: swept %d place(s) in %d request(s): %d job(s) listed, %d new; %d connection(s) "
                "the proxy could not open, each asked again; ended on empty pages: %s", len(sweeps),
                report.sweep_requests, len(jobs), report.new_jobs,
                sum(each.refused_connections for each in sweeps),
                [each.place for each in sweeps if each.unsure_end] or "none")
    if truncated := [each.place for each in sweeps if each.truncated]:
        logger.warning("linkedin: the sweep of %s reached LinkedIn's 1,000th job, so the oldest of its "
                       "window went unread", truncated)


def _ingest(corpus: CorpusRepository, links) -> int:
    """Dedup and upsert, the same four layers the shared cycle applies."""
    new = 0
    for link in links:
        if not link.url:
            continue
        if corpus.find_duplicate(link.url, link.title, link.company, link.location,
                                 application_url=link.application_url) is not None:
            continue
        if corpus.add(**link.as_corpus_row()) is not None:
            new += 1
    return new
