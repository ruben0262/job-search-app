"""One harvest cycle, start to finish. The shared loop.

**A one-shot run, not a daemon with a sleep.** An external timer invokes it; it does one cycle and
exits. That choice buys four things:

1. **Restart safety for free.** A crashed run is a run that did not happen, and the next tick starts
   clean. A long-lived loop that dies stops harvesting *silently* until somebody notices — the
   failure class `OPERATIONS_PLAN.md` §4 exists to prevent.
2. **It composes with Phase 2 rather than being replaced by it.** When `libs/queue` lands, this
   function becomes the body of a queue worker. An internal timer would be thrown away.
3. **It is testable.** `TEST_STRATEGY.md` §2.1 forbids `sleep` in tests, which rules out testing a
   loop that contains one. A function is just called.
4. **Each run is its own log line and its own exit code.**

The cost is that a timer can fire while the previous run is still going, so the cycle takes a
Postgres advisory lock. Overlapping harvests would double the fan-out budget, double the proxy
spend, and race each other on dedup — the lock is what makes "every 15 minutes" mean "at most one at
a time, every 15 minutes".

The stages are `HARVESTER_PLAN.md` §4, in order:

    1. build the QueryKey set from active user configs   (cheap, in memory)
    2. apply the fan-out budget + round-robin selection
    3. execute the shared sources
    4. dedup against the corpus — url → canonical → apply-url → content_key
    5. upsert survivors
    6. embed the new rows via /embed/bulk, once, for everyone

**Per-user scoring is not in this loop.** It is a separate, cheap pass — mixing them would make the
shared harvest wait on N users' models.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session

from libs.contracts.embedder import EmbedderClient, EmbedderVersion
from libs.corpus.ats import AtsRepository
from libs.corpus.repository import CorpusRepository
from libs.tenant.repository import TenantRepository
from services.harvester.embedding import embed_all_pending
from services.harvester.query_set import (
    HARVEST_MAX_QUERIES_PER_CYCLE,
    PlannedQuery,
    QueryKey,
    build_query_set,
    select_within_budget,
)
from services.harvester.sources import OUTER_CONCURRENCY, JobLink, Source, Tier, sources_for

logger = logging.getLogger(__name__)

# One arbitrary constant, shared by every harvester process. Postgres advisory locks are keyed on a
# bigint, and this is simply "the shared harvest cycle".
HARVEST_LOCK_KEY = 0x6A6F_6273_0001

# The platform's shared cadence. Universal, not per-user, and that is not a simplification: there is
# no such thing as harvesting "for" one user when the corpus is shared. What users choose is how
# often they are *told* (`users.notify_interval_min`), which is a different loop entirely.
HARVEST_INTERVAL_MIN = 15


@dataclass
class CycleReport:
    """What one cycle did. Returned rather than only logged, so a caller can assert on it."""

    queries_planned: int = 0
    queries_deferred: int = 0
    links_found: int = 0
    duplicates: dict[str, int] = field(default_factory=dict)
    new_jobs: int = 0
    embedded: int = 0
    source_failures: dict[str, str] = field(default_factory=dict)
    skipped_locked: bool = False

    @property
    def duplicates_total(self) -> int:
        return sum(self.duplicates.values())


@contextmanager
def harvest_lock(session: Session, *, key: int = HARVEST_LOCK_KEY):
    """Hold the cycle lock, or yield False if another run already has it.

    `pg_try_advisory_lock` rather than the blocking form: a timer firing while the previous run is
    still going should **skip**, not queue up behind it. Queueing turns one slow cycle into a
    backlog of cycles all doing the same work, and the fan-out budget is per cycle — two at once
    spends it twice.

    Session-scoped, so it is released even if the process is killed: Postgres drops it when the
    connection goes.
    """
    acquired = bool(session.execute(
        text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar())
    try:
        yield acquired
    finally:
        if acquired:
            try:
                session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            except Exception:
                # **Never let cleanup mask the failure that caused it.** If the cycle raised, the
                # transaction is already aborted and this unlock raises
                # `InFailedSqlTransaction` - which then propagates INSTEAD of the real error, so
                # the log shows a lock problem and hides a schema one. That happened on the first
                # real cycle and cost a run to diagnose.
                #
                # Swallowing it is safe: the advisory lock is session-scoped, so Postgres releases
                # it when the connection closes regardless.
                logger.debug("advisory unlock failed; the connection close will release it",
                             exc_info=True)


def _run_source(source: Source, cfg: dict, repo: AtsRepository) -> list[JobLink]:
    """Invoke one adapter. **Never raises** — the registry's contract.

    Sources break upstream constantly: a changed JSON path, an expired certificate, a rate limit. A
    cycle that aborts on the first one delivers nothing to anybody, so a failure is logged and the
    other sources continue.
    """
    kwargs = {"repo": repo} if source.needs_ats_repo else {}
    return source.adapter(cfg, logger, **kwargs)


def run_cycle(
    session: Session,
    *,
    embedder: EmbedderClient | None = None,
    expected_embedder: EmbedderVersion | None = None,
    source_configs: dict[str, dict] | None = None,
    carried_over: Iterable[QueryKey] = (),
    max_queries: int = HARVEST_MAX_QUERIES_PER_CYCLE,
    run_source: Callable[[Source, dict, AtsRepository], list[JobLink]] = _run_source,
) -> tuple[CycleReport, list[QueryKey]]:
    """Run one shared harvest cycle. Returns the report and the queries to carry into the next.

    `run_source` is injectable so the orchestration can be tested without a network — the adapters
    themselves are covered structurally (`tests/test_harvester_sources.py`), and what is worth
    testing here is the *order* and the *budget*, not whether Greenhouse's JSON parsed.

    Deferred queries are returned rather than stored: they are advice for the next cycle, and
    persisting them would mean reconciling a stored plan against configs that have since changed.
    """
    report = CycleReport()
    corpus = CorpusRepository(session)
    tenant = TenantRepository(session)
    ats = AtsRepository(session)
    source_configs = source_configs or {}

    with harvest_lock(session) as acquired:
        if not acquired:
            logger.warning("harvest: another cycle holds the lock, skipping this tick")
            report.skipped_locked = True
            return report, list(carried_over)

        # 1-2. Demand, folded and budgeted. Dormant and unverified accounts are excluded by
        # active_configs, so an abandoned account stops costing anything (G11).
        configs = tenant.active_configs()
        query_set = build_query_set(configs, sources=("indeed", "google"))
        planned, deferred = select_within_budget(
            query_set, max_queries=max_queries, carried_over=carried_over)
        report.queries_planned = len(planned)
        report.queries_deferred = len(deferred)

        # 3. Execute the shared tier. The per-user LinkedIn loop is deliberately absent: it runs on
        # its owner's schedule with their credential, and cannot be reached from here.
        links = _gather(source_configs, planned, ats, run_source, report)
        report.links_found = len(links)

        # 4-5. Dedup and upsert.
        report.new_jobs = _ingest(corpus, links, report)

        # 6. Embed the new rows. Once, for everyone.
        if embedder is not None:
            result = embed_all_pending(corpus, embedder, expected=expected_embedder)
            report.embedded = result.embedded

    logger.info(
        "harvest: %d queries (%d deferred), %d links, %d duplicates, %d new, %d embedded",
        report.queries_planned, report.queries_deferred, report.links_found,
        report.duplicates_total, report.new_jobs, report.embedded)
    return report, [q.key for q in deferred]


def _gather(source_configs: dict[str, dict], planned: list[PlannedQuery], ats: AtsRepository,
            run_source: Callable[..., list[JobLink]], report: CycleReport) -> list[JobLink]:
    """Run every enabled shared source concurrently.

    `OUTER_CONCURRENCY` is capped at 8 from the 2026-07-24 incident: JobSpy sources open their own
    nested pools, so an unbounded outer fan-out spawned ~180 threads per scrape.

    **The ATS repository is not passed into the threads.** A SQLAlchemy session is not thread-safe,
    and the fan-out here is network-bound; the ATS family runs on the calling thread for that
    reason, and everything else runs in the pool.
    """
    queries_by_source: dict[str, list[PlannedQuery]] = {}
    for query in planned:
        queries_by_source.setdefault(query.key.source, []).append(query)

    shared = [s for s in sources_for(Tier.SHARED) if source_configs.get(s.key, {}).get("enabled")]
    links: list[JobLink] = []

    def _config_for(source: Source) -> dict:
        cfg = dict(source_configs.get(source.key, {}))
        queries = queries_by_source.get(source.key)
        if queries:
            cfg["queries"] = [
                {"search_term": q.key.keyword_norm, "location": q.key.location_norm or None,
                 "is_remote": q.key.is_remote}
                for q in queries]
            cfg["hours_old"] = max(q.key.max_age_hours for q in queries)
        return cfg

    db_bound = [s for s in shared if s.needs_ats_repo]
    network_only = [s for s in shared if not s.needs_ats_repo]

    for source in db_bound:
        try:
            links.extend(run_source(source, _config_for(source), ats))
        except Exception as exc:                                    # noqa: BLE001
            logger.warning("source %s failed: %s", source.key, exc)
            report.source_failures[source.key] = str(exc)[:200]

    if network_only:
        with ThreadPoolExecutor(max_workers=min(OUTER_CONCURRENCY, len(network_only))) as pool:
            futures = {pool.submit(run_source, s, _config_for(s), ats): s for s in network_only}
            for future, source in futures.items():
                try:
                    links.extend(future.result())
                except Exception as exc:                            # noqa: BLE001
                    logger.warning("source %s failed: %s", source.key, exc)
                    report.source_failures[source.key] = str(exc)[:200]
    return links


def _ingest(corpus: CorpusRepository, links: Iterable[JobLink], report: CycleReport) -> int:
    """Dedup each candidate against the corpus, then upsert the survivors.

    Dedup happens **before** insert rather than as a constraint, matching the reference: the derived
    keys are indexed but not unique, so a candidate that collides is skipped rather than raising in
    the middle of a cycle.

    `add()` returns None when the url is already present, which covers the race between the check
    and the insert without a lock.
    """
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
            corpus.touch_last_seen([duplicate.job_id])
            continue

        job_id = corpus.add(**link.as_corpus_row())
        if job_id is None:
            # Inserted by a concurrent path between the check and the insert. Not an error.
            report.duplicates["race"] = report.duplicates.get("race", 0) + 1
            continue
        new_jobs += 1
        if link.snippet:
            # Sources that supply a usable snippet save a whole fetch per job — the difference
            # between a 15-minute cycle and an hour, and on the proxied tier a bandwidth saving too.
            corpus.set_description(job_id, link.snippet)
        if link.application_url:
            corpus.set_application_url(job_id, link.application_url)
    return new_jobs
