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
from dataclasses import dataclass, field
from datetime import UTC, datetime

from libs import limits
from libs.contracts.config import location_search_name
from libs.corpus.repository import CorpusRepository
from libs.db import find
from libs.db.models import User
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

logger = logging.getLogger(__name__)

# How long the pass's describing may run, derived from the timer that starts it. Once per pass, after
# every due search: it used to be once per account, so four accounts could take four times this.
DESCRIBE_BUDGET_SECONDS = PLATFORM_TICK_MIN * 60 * 2 / 3

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
    failures: dict[str, str] = field(default_factory=dict)


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


def run_for_due_users(db, *, now: datetime | None = None,
                      gather=jobspy_source.gather_linkedin) -> LinkedInReport:
    """One pass: the searches of everyone due, pooled, then every posting still to describe."""
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
    for user in due_users(db, now=now):
        report.considered += 1
        interval = limits.resolve(user, "linkedin_interval_min")
        windows[user.id] = search_window(user, now=now, interval_min=interval, most=most)
        # Scheduled first, deliberately: every path below wants the same next run, success or not.
        db[User.COLLECTION].update_one({"_id": user.id}, {"$set": {
            "linkedin_next_run_at": next_linkedin_run(now, interval)}})
        if config := tenants.get_config(user.id):
            configs[user.id] = config
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

    # Every pass, whoever was due: what this pass stored and whatever an earlier one could not reach.
    _describe(corpus, proxy, report, deliverable_for=deliverable_for)
    audit(logger, "linkedin.pass", considered=report.considered, ran=report.ran, queries=report.queries,
          shared=report.shared, links=report.links_found, new=report.new_jobs, described=report.described,
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
