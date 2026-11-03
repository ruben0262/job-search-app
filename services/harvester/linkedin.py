"""The LinkedIn pass: everyone's words, each distinct search asked once, through the site's one proxy
(`libs.proxy`), then every new posting described. No bandwidth limit per person (owner, 2026-11-01).

**Searches are pooled, not per person** (owner, 2026-11-03). Each account still has its own schedule
(`linkedin_interval_min`), but the searches of everyone due in a pass are folded into the distinct
(words, place, remote) queries they make (`build_query_set`) and each is asked once, however many
people share it. The cost of a pass follows the distinct searches, which repeat as people share their
fields, not the number of accounts; the corpus is shared, so a job one search finds reaches everyone
whose words it has.
"""
from __future__ import annotations

import logging
import math
import random
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
from bs4 import BeautifulSoup

from libs import limits
from libs.contracts.config import location_search_name, migrate_config
from libs.corpus.postings import LI_BACKOFF, LI_RETRIES
from libs.corpus.repository import CorpusRepository
from libs.db import find
from libs.db.models import User
from libs.matching import found, green_words, pattern
from libs.obs import audit
from libs.proxy import ENV as PROXY_ENV
from libs.proxy import site_proxy
from libs.secrets import redacting
from libs.settings import (
    PLATFORM_TICK_MIN,
    freshness_hours,
    queries_per_cycle,
    results_per_query,
)
from libs.settings import get as settings_get
from libs.tenant.repository import TenantRepository
from services.harvester.describe import describe_pending
from services.harvester.fetch import make_site_client
from services.harvester.governor import linkedin_enabled, next_linkedin_run
from services.harvester.query_set import build_query_set, original_terms
from services.harvester.sources import jobspy_source
from services.harvester.sources.base import JobLink

logger = logging.getLogger(__name__)

# How long the pass's describing may run, derived from the timer that starts it. Once per pass, after
# every due search: it used to be once per account, so four accounts could take four times this.
DESCRIBE_BUDGET_SECONDS = PLATFORM_TICK_MIN * 60 * 2 / 3

# LinkedIn's guest job list: ten jobs a page, and nothing past the thousandth.
SWEEP_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
SWEEP_PAGE, SWEEP_DEPTH = 10, 1000
# LinkedIn answers a refusal with the same empty page as the end of its list: a sweep of Germany's last
# hour stopped at 20 of ~300 jobs that way (2026-11-03). After a full page, an empty one is asked again,
# each time from another of the proxy's addresses, before it counts as the end.
EMPTY_RETRIES = 3

# LinkedIn pages fetched at once while describing. Measured through the site proxy, 2026-11-02: one at
# a time took 1.1–1.4 s a page, four took 0.24–0.47 s and read all 50 asked, eight took 0.17–0.63 s and
# gave up on 3 of 24 in one run. The rate limit answers 429 at any count; the retries absorb it.
DESCRIBE_WORKERS = 4


@dataclass
class LinkedInReport:
    """What one pass did."""

    considered: int = 0
    ran: int = 0
    # Distinct searches asked, and how many of them more than one person wanted.
    queries: int = 0
    shared: int = 0
    links_found: int = 0
    new_jobs: int = 0
    described: int = 0
    # The sweep: places read with no word, requests it took, jobs it read, and the ones it kept.
    swept_places: int = 0
    sweep_requests: int = 0
    swept: int = 0
    sweep_kept: int = 0
    failures: dict[str, str] = field(default_factory=dict)


@dataclass
class Sweep:
    """What one sweep of one place read."""

    place: str
    requests: int = 0
    read: int = 0
    kept: list[JobLink] = field(default_factory=list)
    # Stopped at LinkedIn's thousandth job: the window held more than one list can be read to.
    truncated: bool = False
    # Stopped on empty pages after a full one, the retries spent: the end, or LinkedIn refusing.
    unsure_end: bool = False


def due_users(db, *, now: datetime) -> list[User]:
    """Active users whose LinkedIn run is due, soonest first."""
    # Ascending puts an empty `linkedin_next_run_at` first: never run is the most overdue.
    return find(db, User, {"active": True, "status": "active",
                           "$or": [{"linkedin_next_run_at": None}, {"linkedin_next_run_at": {"$lte": now}}]},
                sort=[("linkedin_next_run_at", 1)])


def pooled_queries(configs: dict, *, budgets: dict, windows: dict[object, int], cap: int,
                   rng: random.Random | None = None) -> list[dict]:
    """Every distinct search the due people's configs make, once each, however many share it.

    Each reaches back as far as the widest of its people's windows (`search_window`), so nobody's gap is
    left unsearched. The most-shared go first; among equals the order is new each pass, so a search left
    out by `cap` (`queries_per_cycle`) one pass is not the one left out every pass.
    """
    keys = list(build_query_set(configs, sources=("linkedin",), budgets=budgets).items())
    (rng or random).shuffle(keys)
    keys.sort(key=lambda item: -len(item[1]))
    terms = original_terms(configs)
    return [{"search_term": terms.get(key.keyword_norm, key.keyword_norm),
             # The board wants a place name, not the slug we store.
             "location": location_search_name(key.location_norm) or None,
             "is_remote": key.is_remote,
             "hours_old": max(windows[user] for user in users),
             "people": len(users)}
            for key, users in keys[:cap]]


def search_window(user: User, *, now: datetime, interval_min: int, most: int) -> int:
    """How far back, in whole hours, this person's LinkedIn search reaches: to their previous search,
    at least an hour (the finest LinkedIn's search takes here), at most `most`, the operator's window.

    LinkedIn lists a query's matches by relevance, not date, and reads stop at a depth. Over the 4 hours
    every search used to cover, "python developer" in the EU had 200+ jobs and the first 30 held 1 of
    the 23 posted in the last hour; "software engineer" 3 of 47, "data engineer" 0 of 28 (2026-11-02).
    A window as wide as the gap since the last search holds 23–47 a query, which a read of 50 takes
    whole. A search that was due a while ago (the harvester was down) reaches back over the gap.
    """
    if user.linkedin_next_run_at is None:
        return most
    since_last = (now - user.linkedin_next_run_at).total_seconds() + interval_min * 60
    return max(1, min(most, math.ceil(since_last / 3600)))


def sweep_window(user: User, *, now: datetime, interval_min: int, most: int) -> int:
    """How far back, in seconds, a sweep of this person's places reaches: to their previous pass and
    one run before it, so a job listed at the edge of one window is inside the next. Measured
    2026-11-03: a sweep missed 8 of 20 jobs listed exactly an hour before, and a wider window held all 8.
    """
    if user.linkedin_next_run_at is None:
        return most * 3600
    since_last = (now - user.linkedin_next_run_at).total_seconds() + interval_min * 60
    return int(min(most * 3600, since_last + PLATFORM_TICK_MIN * 60))


def sweep_places(places: dict[str, int], words: list[str], *, proxy: str) -> list[Sweep]:
    """Every job each place listed in its window (seconds), newest first, with no search word; the ones
    whose title has somebody's word are kept.

    A word search costs a request per word and place, more with every new word. A sweep costs a request
    per ten jobs the place lists, the same however many words there are. Measured 2026-11-03, one hour:
    Ireland 9 jobs, the Netherlands 35, Germany ~300, the UK ~410, the EU 937+ (a list is read to 1,000).
    Only titles come back, so a job whose title has none of anybody's words is not stored, and the word
    searches still find the ones whose description has it: 46–54% of LinkedIn matches were description
    only, and 88% for skills, on the 6,406 LinkedIn jobs here with a description.
    """
    regex = pattern(words)
    if regex is None:
        return []
    with redacting(proxy), make_site_client(proxy) as client:
        return [_sweep(client, place, seconds, regex) for place, seconds in places.items()]


def _sweep(client, place: str, seconds: int, regex) -> Sweep:
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
        sweep.read += len(cards)
        sweep.kept += [card for card in cards if found(regex, card.title)]
        if len(cards) < SWEEP_PAGE:
            return sweep
        start += SWEEP_PAGE
    sweep.truncated = True
    return sweep


def _page(client, params: dict, sweep: Sweep) -> httpx.Response | None:
    """One page of the list, asked again after a 429, each time from the proxy's next address."""
    response = None
    for attempt in range(LI_RETRIES):
        sweep.requests += 1
        try:
            response = client.get(SWEEP_URL, params=params)
        except httpx.HTTPError as exc:
            logger.warning("linkedin: a sweep page of %s could not be read: %s", sweep.place,
                           type(exc).__name__)
            return None
        if response.status_code != 429:
            return response
        time.sleep(LI_BACKOFF * (attempt + 1))
    return response


def _cards(html: str) -> list[JobLink]:
    """The jobs on one page of the list: title, company, place and day, never the description."""
    out: list[JobLink] = []
    for card in BeautifulSoup(html, "html.parser").find_all(class_="base-search-card"):
        job_id = str(card.get("data-entity-urn", "")).rsplit(":", 1)[-1]
        title = card.find("h3", class_="base-search-card__title")
        if not job_id.isdigit() or title is None:
            continue
        company = card.find("h4", class_="base-search-card__subtitle")
        where = card.find("span", class_="job-search-card__location")
        posted = card.find("time")
        # The address the word searches store, so one job found both ways is one job.
        out.append(JobLink(url=f"https://www.linkedin.com/jobs/view/{job_id}",
                           title=title.get_text(strip=True),
                           company=company.get_text(strip=True) if company else "",
                           location=where.get_text(strip=True) if where else "", source="linkedin",
                           date_posted=posted.get("datetime") if posted else None, found_via="sweep"))
    return out


def run_for_due_users(db, *, now: datetime | None = None,
                      gather=jobspy_source.gather_linkedin, sweep=None) -> LinkedInReport:
    """One pass: the searches of everyone due, pooled; a sweep of their places for titles with anybody's
    words; then every posting still to describe. `sweep` is `sweep_places`, looked up when called."""
    report = LinkedInReport()
    if not linkedin_enabled():
        logger.warning("linkedin: tier disabled by HARVEST_LINKEDIN_DISABLED")
        return report
    proxy = site_proxy()
    if proxy is None:
        logger.warning("linkedin: %s is not set, so nobody's LinkedIn search ran", PROXY_ENV)
        return report

    now = now or datetime.now(UTC)
    corpus = CorpusRepository(db)
    # Read once per pass rather than per user: both are platform settings, not per-account ones. The
    # window is the most a search reaches back (`search_window`).
    most = freshness_hours(db, "linkedin")
    depth = results_per_query(db, "linkedin")
    # **Not `window`.** `freshness_hours` is how far back to *search*; `job_max_age_hours` is how
    # old a posting may be and still reach anybody. Describing is bounded by the second, because a
    # row past it has already been refused by the pre-score filter and its text will never be read.
    deliverable_for = int(settings_get(db, "job_max_age_hours"))

    tenants = TenantRepository(db)
    configs: dict = {}
    budgets: dict = {}
    windows: dict = {}
    # Each place someone due wants, and how far back its sweep reaches: the widest of theirs.
    places: dict[str, int] = {}
    for user in due_users(db, now=now):
        report.considered += 1
        interval = limits.resolve(user, "linkedin_interval_min")
        windows[user.id] = search_window(user, now=now, interval_min=interval, most=most)
        reach = sweep_window(user, now=now, interval_min=interval, most=most)
        # Scheduled first, deliberately: every path below wants the same next run, success or not.
        db[User.COLLECTION].update_one({"_id": user.id}, {"$set": {
            "linkedin_next_run_at": next_linkedin_run(now, interval)}})
        if config := tenants.get_config(user.id):
            configs[user.id] = config
            for section in migrate_config(config).get("sections") or []:
                for name in filter(None, map(location_search_name, section.get("locations") or [])):
                    places[name] = max(places.get(name, 0), reach)
            budgets[user.id] = limits.resolve(user, "keywords")
            # `None` is Unlimited, and `build_query_set` takes it as every word the account has.

    queries = pooled_queries(configs, budgets=budgets, windows=windows,
                             cap=queries_per_cycle(db, "linkedin"))
    if queries:
        try:
            _search(queries, corpus=corpus, proxy=proxy, report=report, gather=gather,
                    results_wanted=depth, people=len(configs))
            report.ran = len(configs)
        except Exception as exc:                                    # noqa: BLE001
            # The search failing is not the describing failing: what earlier passes stored still waits.
            report.failures["search"] = f"{type(exc).__name__}: {exc}"
            logger.warning("linkedin: the pooled search failed: %s", type(exc).__name__)

    if places:
        # Everyone's words, not only the due people's: what a sweep keeps is shared by everybody.
        words = sorted({word for config in tenants.active_configs().values() for word in green_words(config)})
        try:
            _swept((sweep or sweep_places)(places, words, proxy=proxy), corpus=corpus, report=report)
        except Exception as exc:                                    # noqa: BLE001
            report.failures["sweep"] = f"{type(exc).__name__}: {exc}"
            logger.warning("linkedin: the sweep failed: %s", type(exc).__name__)

    # Every pass, whoever was due: what this pass stored and whatever an earlier one could not reach.
    _describe(corpus, proxy, report, deliverable_for=deliverable_for)
    audit(logger, "linkedin.pass", considered=report.considered, ran=report.ran, queries=report.queries,
          shared=report.shared, links=report.links_found, new=report.new_jobs, described=report.described,
          swept=report.swept, sweep_kept=report.sweep_kept, sweep_requests=report.sweep_requests,
          failures=len(report.failures))
    return report


def _search(queries: list[dict], *, corpus: CorpusRepository, proxy: str, report: LinkedInReport,
            gather, results_wanted: int, people: int) -> None:
    """The pooled searches, through the site proxy, and what they found stored once."""
    with redacting(proxy):
        cfg = {"queries": queries,
               # The operator's ceiling, not the constant beside it.
               "results_wanted": results_wanted,
               # Never from jobspy, which would fetch every result again, the ones already held too.
               "linkedin_fetch_description": False}
        links = gather(cfg, logger, proxy=proxy)
    new_jobs = _ingest(corpus, links)
    report.queries = len(queries)
    report.shared = sum(1 for query in queries if query["people"] > 1)
    report.links_found += len(links)
    report.new_jobs += new_jobs
    logger.info("linkedin: %d distinct search(es) for %d people (%d shared), %d link(s), %d new",
                len(queries), people, report.shared, len(links), new_jobs)


def _swept(sweeps: list[Sweep], *, corpus: CorpusRepository, report: LinkedInReport) -> None:
    """What the sweeps kept, stored once, and one line saying what they read."""
    kept = [link for sweep in sweeps for link in sweep.kept]
    new = _ingest(corpus, kept)
    report.swept_places += len(sweeps)
    report.sweep_requests += sum(sweep.requests for sweep in sweeps)
    report.swept += sum(sweep.read for sweep in sweeps)
    report.sweep_kept += len(kept)
    report.new_jobs += new
    logger.info("linkedin: swept %d place(s) in %d request(s): %d job(s) read, %d with somebody's word in "
                "the title, %d new; ended on empty pages: %s", len(sweeps), report.sweep_requests,
                report.swept, len(kept), new, [sweep.place for sweep in sweeps if sweep.unsure_end] or "none")
    if truncated := [sweep.place for sweep in sweeps if sweep.truncated]:
        logger.warning("linkedin: the sweep of %s reached LinkedIn's 1,000th job, so the oldest of its "
                       "window went unread", truncated)


def _describe(corpus: CorpusRepository, proxy: str, report: LinkedInReport, *,
              deliverable_for: int) -> None:
    """Every LinkedIn posting still without its text, newest first, until the budget runs out.

    LinkedIn's search results carry no text, so each posting is opened to read it, and until then
    it can match on its title only. There is no count ceiling: there is no bandwidth limit to keep
    it under (owner, 2026-11-01), and the budget is what bounds the pass. A separate catch-up pass
    with no timer used to exist for what this missed; it never ran, and this leaves nothing for it.
    """
    with redacting(proxy), make_site_client(proxy) as client:
        described = describe_pending(corpus, client, linkedin=True, limit=None,
                                     budget_seconds=DESCRIBE_BUDGET_SECONDS,
                                     max_age_hours=deliverable_for, workers=DESCRIBE_WORKERS)
    report.described += described.described
    if described.proxy_error:
        logger.error("linkedin: describing stopped, the site proxy failed: %s", described.proxy_error[:200])
    logger.info("linkedin: described %d of %d posting(s), %.1f MB", described.described,
                described.attempted, client.megabytes_downloaded)


def _ingest(corpus: CorpusRepository, links) -> int:
    """Dedup and upsert, the same four layers the shared cycle applies."""
    new = 0
    for link in links:
        if not link.url:
            continue
        if corpus.find_duplicate(link.url, link.title, link.company, link.location,
                                 application_url=link.application_url) is not None:
            continue
        job_id = corpus.add(**link.as_corpus_row())
        if job_id is None:
            continue
        new += 1
        # The snippet is not a description and is not stored as one.
        if link.application_url:
            corpus.set_application_url(job_id, link.application_url)
    return new
