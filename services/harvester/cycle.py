"""One harvest cycle, start to finish."""
from __future__ import annotations

import logging
import uuid as _uuid
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
from libs.corpus.repository import CorpusRepository
from libs.db import find, get, lease
from libs.db.models import CorpusJob, User
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
from services.harvester.query_set import (
    PlannedQuery,
    QueryKey,
    build_query_set,
    original_terms,
    select_within_budget,
)
from services.harvester.source_config import QUERY_SHAPES, QUERY_TAKING_SOURCES
from services.harvester.sources import OUTER_CONCURRENCY, JobLink, Source, Tier, sources_for

logger = logging.getLogger(__name__)


@dataclass
class CycleReport:
    """What one cycle did. Returned rather than only logged, so a caller can assert on it."""

    queries_planned: int = 0
    queries_deferred: int = 0
    links_found: int = 0
    duplicates: dict[str, int] = field(default_factory=dict)
    new_jobs: int = 0
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



# The pseudo-account operator keywords are folded in under.
OPERATOR_DEMAND_ID = _uuid.UUID("00000000-0000-0000-0000-0000000000ff")


def _operator_demand(db) -> dict:
    """Operator keywords, shaped like a user config so the fold needs no special case."""
    from libs.db.models import OperatorKeyword  # noqa: PLC0415 - avoids a cycle at import time

    rows = find(db, OperatorKeyword, {})
    if not rows:
        return {}

    by_place: dict[str, list[str]] = {}
    for row in rows:
        by_place.setdefault(row.location or "", []).append(row.keyword)

    sections = [{"keywords": sorted(set(terms)),
                 "locations": [place] if place else [],
                 "platform": "all"}
                for place, terms in sorted(by_place.items())]
    return {OPERATOR_DEMAND_ID: {"sections": sections}}

def run_cycle(
    db: Database,
    *,
    source_configs: dict[str, dict] | None = None,
    carried_over: Iterable[QueryKey] = (),
    limits: dict[str, int] | None = None,
    describe_limit: int = DESCRIBE_PER_CYCLE_DEFAULT,
    run_source: Callable[[Source, dict, AtsRepository], list[JobLink]] = _run_source,
    describe: bool = True,
) -> tuple[CycleReport, list[QueryKey]]:
    """Run one shared harvest cycle. Nothing is embedded or scored: matching happens when it is read."""
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
            return report, list(carried_over)

        # 1-2. Every active account's config, which is what the query set is folded from.
        configs = tenant.active_configs()

        # Operator-injected demand, folded in as one more participant.
        configs = {**configs, **_operator_demand(db)}
        # The sources that are actually enabled and actually read a query set.
        askable = tuple(sorted(QUERY_TAKING_SOURCES & set(source_configs)))
        # No location axis at all, decided 2026-08-04.
        query_set = build_query_set(configs, sources=askable,
                                    locating_sources=(), remote_aware_sources=(),
                                    budgets=_keyword_budgets(db, configs))
        planned, deferred = select_within_budget(
            query_set, limits=limits, carried_over=carried_over)
        report.queries_planned = len(planned)
        report.queries_deferred = len(deferred)
        if not configs:
            # A cycle with no active accounts finishes instantly and reads like a healthy quiet run.
            logger.warning("harvest: no active accounts — this cycle will fetch nothing")
        elif not planned:
            logger.warning("harvest: %d account(s) but no queries planned — check their configs",
                           len(configs))

        # 3. Fetch the shared tier. The per-user LinkedIn loop runs on its own interval.
        with timed("gather", report.timings):
            links = _gather(source_configs, planned, ats, run_source, report,
                            terms=original_terms(configs))
        report.links_found = len(links)

        # 4-5. Dedup and upsert.
        with timed("ingest", report.timings):
            report.new_jobs = _ingest(corpus, links, report)

        # 5b. Fetch the postings' own text for rows that arrived with a thin snippet.
        if describe:
            with timed("describe", report.timings), make_shared_client() as client:
                described = describe_pending(corpus, client, linkedin=False,
                                             limit=describe_limit)
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
        "harvest: %d queries (%d deferred), %d links, %d duplicates, %d new, %d described",
        report.queries_planned, report.queries_deferred, report.links_found,
        report.duplicates_total, report.new_jobs, report.described)
    report_timings(report.timings, event="harvest.timing", log=logger,
                   links=report.links_found, new=report.new_jobs)

    _warn_if_near_budget(monotonic() - started, report)
    audit(logger, "harvest.cycle",
          queries=report.queries_planned, links=report.links_found,
          new=report.new_jobs, described=report.described,
          ats_tokens_found=report.ats_tokens_found, archived=report.archived,
          failed_sources=len(report.source_failures),
          empty_sources=sum(1 for n in report.links_by_source.values() if n == 0))
    return report, [q.key for q in deferred]


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


def _gather(source_configs: dict[str, dict], planned: list[PlannedQuery], ats: AtsRepository,
            run_source: Callable[..., list[JobLink]], report: CycleReport,
            *, terms: dict[str, str] | None = None) -> list[JobLink]:
    """Run every enabled shared source concurrently."""
    queries_by_source: dict[str, list[PlannedQuery]] = {}
    for query in planned:
        queries_by_source.setdefault(query.key.source, []).append(query)

    shared = [s for s in sources_for(Tier.SHARED) if source_configs.get(s.key, {}).get("enabled")]
    links: list[JobLink] = []
    if not shared:
        # Every source disabled looks exactly like every source finding nothing.
        logger.warning("harvest: no shared sources are enabled — nothing will be fetched")

    def _config_for(source: Source) -> dict:
        cfg = dict(source_configs.get(source.key, {}))
        queries = queries_by_source.get(source.key)
        if queries:
            # Rendered in the shape this adapter reads.
            shape = QUERY_SHAPES[source.key]
            cfg["queries"] = [
                # The phrase the user wrote, not the fold key.
                shape.render(term=(terms or {}).get(q.key.keyword_norm, q.key.keyword_norm),
                             location=q.key.location_norm, is_remote=q.key.is_remote)
                for q in queries]
            cfg["hours_old"] = max(q.key.max_age_hours for q in queries)
        return cfg

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
        logger.warning("source %s returned nothing — check it was actually asked for something",
                       source.key)


def _ingest(corpus: CorpusRepository, links: Iterable[JobLink], report: CycleReport) -> int:
    """Dedup each candidate against the corpus, then upsert the survivors."""
    new_jobs = 0
    for link in links:
        if not link.url:
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


def _keyword_budgets(db, configs) -> dict:
    """Each account's own `keywords` ceiling, so the panel's value is the one that harvests."""
    from libs import limits  # noqa: PLC0415 - one caller, kept local

    out = {}
    for user_id in configs:
        user = get(db, User, user_id)
        # Operator-injected demand has no user row and keeps the catalogue default.
        out[user_id] = limits.resolve(user, "keywords") if user is not None else None
    return out


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
    """
    since = datetime.now(UTC) - timedelta(hours=ATS_DISCOVERY_LOOKBACK_HOURS)
    urls = [row["url"] for row in db[CorpusJob.COLLECTION].find(
        {"first_seen_at": {"$gte": since}, "url": {"$ne": None}}, {"url": 1}, limit=ATS_DISCOVERY_MAX_ROWS)]

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
