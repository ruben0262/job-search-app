"""One harvest cycle, start to finish."""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from time import monotonic

from pymongo.database import Database
from pymongo.errors import PyMongoError

from libs.corpus.archive import archive_expired
from libs.corpus.ats import AtsRepository
from libs.corpus.ats_urls import tokens_from_urls
from libs.corpus.freshness import platform_windows
from libs.corpus.geo import allows, posting_country, wanted_countries
from libs.corpus.repository import TYPED, CorpusRepository
from libs.db import lease
from libs.db.models import CorpusJob
from libs.obs import Timings, audit, report_timings, timed
from libs.settings import (
    ATS_DISCOVERY_LOOKBACK_HOURS,
    ATS_DISCOVERY_MAX_ROWS,
    HARVEST_CYCLE_BUDGET_SECONDS,
    HARVEST_CYCLE_WARN_FRACTION,
)
from libs.tenant.repository import TenantRepository
from services.harvester.describe import SHARED_LIMIT as DESCRIBE_PER_CYCLE_DEFAULT
from services.harvester.describe import describe_pending
from services.harvester.fetch import make_shared_client
from services.harvester.source_config import union_locations
from services.harvester.sources import OUTER_CONCURRENCY, JobLink, Source, Tier, sources_for

logger = logging.getLogger(__name__)


@dataclass
class CycleReport:
    """What one cycle did. Returned rather than only logged, so a caller can assert on it."""

    links_found: int = 0
    duplicates: dict[str, int] = field(default_factory=dict)
    new_jobs: int = 0
    # Postings in a country nobody picked, not stored (owner, 2026-11-04).
    elsewhere: int = 0
    # Rows whose thin snippet was replaced by the posting's own text.
    described: int = 0
    describe_outcomes: dict[str, int] = field(default_factory=dict)
    # Per source, so "the cycle found 200 links" cannot hide "and four of six sources found none".
    links_by_source: dict[str, int] = field(default_factory=dict)
    source_failures: dict[str, str] = field(default_factory=dict)
    # Company boards learned from posting URLs this cycle, not polled for.
    ats_tokens_found: int = 0
    # Postings written to the archive file and deleted from the corpus.
    archived: int = 0
    skipped_locked: bool = False
    # How long each stage took.
    timings: Timings = field(default_factory=Timings)

    @property
    def duplicates_total(self) -> int:
        return sum(self.duplicates.values())


def harvest_lock(db: Database):
    """Hold the cycle's lease, or yield False if another run already has it. As long as a cycle may
    run, so a cycle that died lets go before the next tick rather than holding the harvest forever."""
    return lease(db, "harvest", HARVEST_CYCLE_BUDGET_SECONDS)


def _run_source(source: Source, cfg: dict, repo: AtsRepository) -> list[JobLink]:
    """Invoke one adapter."""
    kwargs = {"repo": repo} if source.needs_ats_repo else {}
    return source.adapter(cfg, logger, **kwargs)



def run_cycle(
    db: Database,
    *,
    source_configs: dict[str, dict] | None = None,
    describe_limit: int = DESCRIBE_PER_CYCLE_DEFAULT,
    run_source: Callable[[Source, dict, AtsRepository], list[JobLink]] = _run_source,
    describe: bool = True,
) -> CycleReport:
    """Run one shared harvest cycle: every enabled board read whole for its window, what is new stored, the
    thin texts read, company boards learned, old jobs archived. No board is asked for a word (owner,
    2026-11-04): people's words are matched on the stored jobs when they are read."""
    started = monotonic()
    report = CycleReport()
    corpus = CorpusRepository(db)
    tenant = TenantRepository(db)
    ats = AtsRepository(db)
    source_configs = source_configs or {}

    with harvest_lock(db) as acquired:
        if not acquired:
            logger.warning("harvest: another cycle holds the lock, skipping this tick")
            report.skipped_locked = True
            return report

        # 1. Every active account's config: which countries are kept.
        configs = tenant.active_configs()
        if not configs:
            # A cycle with no active accounts keeps nothing, and reads like a healthy quiet run.
            logger.warning("harvest: no active accounts — this cycle keeps nothing")

        # 2. Read every enabled board. LinkedIn is a pass of its own beside this one.
        with timed("gather", report.timings):
            links = _gather(source_configs, ats, run_source, report)
        report.links_found = len(links)

        # 4-5. Dedup and upsert.
        with timed("ingest", report.timings):
            report.new_jobs = _ingest(corpus, links, report,
                                      wanted=wanted_countries(union_locations(configs)))

        # 5b. Fetch the postings' own text for rows that arrived with a thin snippet, and can still be shown.
        if describe:
            with timed("describe", report.timings), make_shared_client() as client:
                described = describe_pending(corpus, client, linkedin=False, limit=describe_limit,
                                             windows=platform_windows(db))
            report.described = described.described
            report.describe_outcomes = described.outcomes

        # 5d. Learn which company boards to poll, from the URLs already stored.
        with timed("ats_discovery", report.timings):
            report.ats_tokens_found = _discover_ats_tokens(db, ats)

        # 6. Retire what has aged out. On this tick rather than a timer of its own: the corpus is
        # the thing that grows and this is the pass that already touches it, and a second clock is
        # a second outage (CLAUDE.md §6). Bounded per pass, so it cannot become the slow stage.
        _archive_expired(db, report)

    logger.info(
        "harvest: %d links, %d duplicates, %d in no country anybody picked, %d new, %d described",
        report.links_found, report.duplicates_total, report.elsewhere, report.new_jobs, report.described)
    report_timings(report.timings, event="harvest.timing", log=logger,
                   links=report.links_found, new=report.new_jobs)

    _warn_if_near_budget(monotonic() - started, report)
    audit(logger, "harvest.cycle", links=report.links_found,
          new=report.new_jobs, elsewhere=report.elsewhere, described=report.described,
          ats_tokens_found=report.ats_tokens_found, archived=report.archived,
          failed_sources=len(report.source_failures),
          empty_sources=sum(1 for n in report.links_by_source.values() if n == 0))
    return report


def _archive_expired(db: Database, report: CycleReport) -> None:
    """Move aged-out postings to the archive file, and never let that stop a harvest.

    Retention failing is not a reason to lose a cycle's intake: nothing is deleted unless it was
    written first, so the rows stay where they are and the next tick tries again. Logged at ERROR
    because the corpus grows without this and nothing else would say so — the symptom of a silent
    failure here is a disk filling up in a month's time.
    """
    try:
        with timed("archive", report.timings):
            report.archived = archive_expired(db).jobs
    except (OSError, PyMongoError) as exc:
        logger.error("harvest: archiving aged-out postings failed, nothing was deleted "
                     "and the corpus keeps growing: %s", exc)


def _warn_if_near_budget(elapsed: float, report: CycleReport) -> None:
    """Say so while there is still margin left to say it in.

    Past the budget `harvester.service`'s `TimeoutStartSec` kills the cycle, and the stages behind
    the overrun are skipped rather than retried — nothing raises, so the only symptom is a corpus
    that quietly stops growing. That is how the 8.4-minute peak of 2026-08-10 was found (6az).
    """
    if elapsed <= HARVEST_CYCLE_BUDGET_SECONDS * HARVEST_CYCLE_WARN_FRACTION:
        return
    slowest = report.timings.slowest()
    logger.warning(
        "harvest: cycle took %.1f min of its %.1f-min budget (%.0f%%), slowest stage %s at %.1f "
        "min — past the budget the cycle is killed and the stages behind the overrun are skipped, "
        "not retried",
        elapsed / 60, HARVEST_CYCLE_BUDGET_SECONDS / 60,
        100 * elapsed / HARVEST_CYCLE_BUDGET_SECONDS,
        slowest[0] if slowest else "none", (slowest[1] / 60_000) if slowest else 0.0)


def _gather(source_configs: dict[str, dict], ats: AtsRepository,
            run_source: Callable[..., list[JobLink]], report: CycleReport) -> list[JobLink]:
    """Run every enabled shared source concurrently."""
    shared = [s for s in sources_for(Tier.SHARED) if source_configs.get(s.key, {}).get("enabled")]
    links: list[JobLink] = []
    if not shared:
        # Every source disabled looks exactly like every source finding nothing.
        logger.warning("harvest: no shared sources are enabled — nothing will be fetched")

    def _config_for(source: Source) -> dict:
        return dict(source_configs.get(source.key, {}))

    db_bound = [s for s in shared if s.needs_ats_repo]
    network_only = [s for s in shared if not s.needs_ats_repo]

    for source in db_bound:
        try:
            found = run_source(source, _config_for(source), ats)
        except Exception as exc:                                    # noqa: BLE001
            logger.warning("source %s failed: %s", source.key, exc)
            report.source_failures[source.key] = str(exc)[:200]
        else:
            _record_source(report, source, found, links)

    if network_only:
        with ThreadPoolExecutor(max_workers=min(OUTER_CONCURRENCY, len(network_only))) as pool:
            futures = {pool.submit(run_source, s, _config_for(s), ats): s for s in network_only}
            for future, source in futures.items():
                try:
                    found = future.result()
                except Exception as exc:                            # noqa: BLE001
                    logger.warning("source %s failed: %s", source.key, exc)
                    report.source_failures[source.key] = str(exc)[:200]
                else:
                    _record_source(report, source, found, links)
    return links


def _record_source(report: CycleReport, source: Source, found: list[JobLink],
                   links: list[JobLink]) -> None:
    """Collect a source's results, and say so when a source that was asked returns nothing."""
    links.extend(found)
    report.links_by_source[source.key] = len(found)
    if not found:
        logger.warning("source %s returned nothing: down, or nothing in its window", source.key)


def _ingest(corpus: CorpusRepository, links: Iterable[JobLink], report: CycleReport, *,
            wanted: frozenset[str] | None = None) -> int:
    """Dedup each candidate against the corpus, then upsert the survivors. A posting in a country nobody
    picked (`wanted`, everybody's countries) is not stored: nobody's search would show it. One whose
    country cannot be told, "Remote" for one, is kept, as the jobs page keeps it (`geo.allows`)."""
    new_jobs = 0
    for link in links:
        if not link.url:
            continue
        if not allows(posting_country(link.location, title=link.title), wanted):
            report.elsewhere += 1
            continue
        duplicate = corpus.find_duplicate(
            link.url, link.title, link.company, link.location,
            application_url=link.application_url)
        if duplicate is not None:
            report.duplicates[duplicate.matched_on] = \
                report.duplicates.get(duplicate.matched_on, 0) + 1
            continue

        job_id = corpus.add(**link.as_corpus_row())
        if job_id is None:
            # Inserted by a concurrent path between the check and the insert. Not an error.
            report.duplicates["race"] = report.duplicates.get("race", 0) + 1
            continue
        new_jobs += 1
        if link.snippet:
            # Sources supplying a usable snippet save a whole fetch per job, and proxy bandwidth with it.
            corpus.set_description(job_id, link.snippet)
        if link.application_url:
            corpus.set_application_url(job_id, link.application_url)
    return new_jobs


def _discover_ats_tokens(db, ats: AtsRepository) -> int:
    """Enrol company boards named by postings the corpus already holds. Returns how many are new.

    **The company list was a closed loop.** Tokens came only from hiring.cafe's `board_token`
    field, and hiring.cafe is queried with our users' keywords — so the boards we polled were the
    boards our existing users had already surfaced, and the ATS tier stayed as narrow as the corpus
    that seeded it. Sampled 2026-08-09: all 336 live tokens were software startups, on a platform
    where nine of seventeen enabled sources are ATS boards polling that list.

    Every other source carries the same signal in the posting URL and it was being discarded. A
    LinkedIn or Adzuna listing for an automotive company links to its Greenhouse board, and the
    token is in the link. Measured 2026-08-10 against the live corpus: 592 companies with an
    ATS-shaped URL, 171 of them boards nobody was polling — a 52% increase on the list, from rows
    already fetched and stored, with no request made.

    It runs over recent rows rather than the whole corpus because it is idempotent and the corpus
    only grows: a token found last week is already enrolled, and `add_tokens` ignores duplicates.

    Both of a posting's links, its own and where it is applied to (2026-11-06): a board's posting often
    sends its applicants to another company's board, and that link was never read. On the laptop's corpus
    the apply links named 29 boards not polled, a tenth more than the 291 that were. A LinkedIn posting
    is read only with an apply link: its own is LinkedIn's, and its guest page gives none (1 of 18,965
    had one). The ~40,000 a day it lists otherwise filled the row cap before any board's posting was read.
    """
    since = datetime.now(UTC) - timedelta(hours=ATS_DISCOVERY_LOOKBACK_HOURS)
    urls = [link for row in db[CorpusJob.COLLECTION].find(
        # A job somebody wrote in names whatever address they typed: not a board to enrol for everyone.
        {"first_seen_at": {"$gte": since}, "source": {"$ne": TYPED},
         "$or": [{"source": {"$ne": "linkedin"}}, {"application_url": {"$ne": None}}]},
        {"url": 1, "application_url": 1}, limit=ATS_DISCOVERY_MAX_ROWS)
        for link in (row.get("url"), row.get("application_url")) if link]

    pairs = tokens_from_urls(urls)
    if not pairs:
        return 0
    added = ats.add_tokens(pairs)
    if added:
        by_ats: dict[str, int] = {}
        for name, _token in pairs:
            by_ats[name] = by_ats.get(name, 0) + 1
        logger.info("harvest: %d new company board(s) from %d posting URLs %s",
                    added, len(urls), by_ats)
    return added
