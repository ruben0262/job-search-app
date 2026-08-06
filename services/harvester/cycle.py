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
import uuid as _uuid
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from libs.contracts.embedder import EmbedderClient, EmbedderVersion
from libs.corpus.ats import AtsRepository
from libs.corpus.repository import CorpusRepository
from libs.db.models import User
from libs.obs import Timings, audit, report_timings, timed
from libs.queue import enqueue
from libs.tenant.repository import TenantRepository
from services.harvester.describe import SHARED_LIMIT as DESCRIBE_PER_CYCLE_DEFAULT
from services.harvester.describe import describe_pending
from services.harvester.embedding import embed_all_pending
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

# One arbitrary constant, shared by every harvester process. Postgres advisory locks are keyed on a
# bigint, and this is simply "the shared harvest cycle".
HARVEST_LOCK_KEY = 0x6A6F_6273_0001

@dataclass
class CycleReport:
    """What one cycle did. Returned rather than only logged, so a caller can assert on it."""

    queries_planned: int = 0
    queries_deferred: int = 0
    links_found: int = 0
    duplicates: dict[str, int] = field(default_factory=dict)
    new_jobs: int = 0
    # Rows whose thin snippet was replaced by the posting's own text. Low with a healthy
    # `describe_outcomes` means the sources are already generous; low with `generic_http_403`
    # everywhere means the pass is being blocked and every label is being formed from a teaser.
    described: int = 0
    describe_outcomes: dict[str, int] = field(default_factory=dict)
    embedded: int = 0
    # Per source, so "the cycle found 200 links" cannot hide "and four of six sources found none".
    links_by_source: dict[str, int] = field(default_factory=dict)
    source_failures: dict[str, str] = field(default_factory=dict)
    # How many accounts were asked to catch up their scores. Zero with active users is
    # a broken enqueue, which would otherwise present as feeds that quietly stop moving.
    scoring_enqueued: int = 0
    skipped_locked: bool = False
    # How long each stage took. "The feed is slow" is not a diagnosis, and the stage everybody
    # guesses is the model call — which on this platform is frequently not the one.
    timings: Timings = field(default_factory=Timings)

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



# The pseudo-account operator keywords are folded in under. A real UUID so `build_query_set` and the
# round-robin treat it as one more user, and a fixed one so it does not fragment across cycles.
OPERATOR_DEMAND_ID = _uuid.UUID("00000000-0000-0000-0000-0000000000ff")


def _operator_demand(session) -> dict:
    """Operator keywords, shaped like a user config so the fold needs no special case.

    Returns `{}` when there are none, which is the normal state and costs nothing.

    Grouped into one section per location so a term with no location contributes a "wherever" query
    -- `build_query_set` already reads an empty location list that way, and that is the right
    default for seeding a field rather than a place.
    """
    from libs.db.models import OperatorKeyword  # noqa: PLC0415 - avoids a cycle at import time

    rows = session.scalars(select(OperatorKeyword)).all()
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
    session: Session,
    *,
    embedder: EmbedderClient | None = None,
    expected_embedder: EmbedderVersion | None = None,
    source_configs: dict[str, dict] | None = None,
    carried_over: Iterable[QueryKey] = (),
    limits: dict[str, int] | None = None,
    describe_limit: int = DESCRIBE_PER_CYCLE_DEFAULT,
    run_source: Callable[[Source, dict, AtsRepository], list[JobLink]] = _run_source,
    describe: bool = True,
) -> tuple[CycleReport, list[QueryKey]]:
    """Run one shared harvest cycle. Returns the report and the queries to carry into the next.

    `describe` turns off the description pass, which is the one stage that makes its own HTTP
    requests outside `run_source`. Tests that inject a fake source set it False rather than have
    every orchestration test reach the network.

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

        # **Operator-injected demand, folded in as one more participant.** The corpus only holds
        # what somebody has already searched for, so an account arriving in a field nobody has
        # searched finds nothing to train on -- which is exactly what happened to the second
        # account here. Seeding ahead of demand is the fix, and it belongs in the same fold as
        # everybody else's terms so it competes for budget on the same round-robin footing rather
        # than jumping the queue.
        configs = {**configs, **_operator_demand(session)}
        # **The sources that are actually enabled and actually read a query set.** This was
        # hardcoded to `("indeed", "google")`, and neither is enabled — nor are those even the
        # registry's keys for them (`jobspy_indeed`, `jobspy_google`). So every planned query
        # carried a source name that `_gather` could never match, and the whole budget — the fold,
        # the round-robin, the carry-over — governed **zero requests** while nine boards that do
        # read `cfg["queries"]` were handed one empty search each.
        #
        # Each axis is passed per source, so a board with no geography is asked once per keyword
        # instead of once per keyword × location for identical results.
        askable = tuple(sorted(QUERY_TAKING_SOURCES & set(source_configs)))
        # **No location axis at all, decided 2026-08-04.** Only one free-tier board has a location
        # model worth the name, every location a user picks multiplies that board's query count,
        # and a posting's country is recoverable from the posting — so it is a filter to apply
        # against the corpus before scoring, not a question to ask a job board. `remote` goes with
        # it for the same reason: it is on the row already.
        query_set = build_query_set(configs, sources=askable,
                                    locating_sources=(), remote_aware_sources=())
        planned, deferred = select_within_budget(
            query_set, limits=limits, carried_over=carried_over)
        report.queries_planned = len(planned)
        report.queries_deferred = len(deferred)
        if not configs:
            # A cycle with no active accounts finishes instantly and reports success, which reads
            # identically to a healthy quiet run. It is not: it means nobody is being served.
            logger.warning("harvest: no active accounts — this cycle will fetch nothing")
        elif not planned:
            logger.warning("harvest: %d account(s) but no queries planned — check their configs",
                           len(configs))

        # 3. Execute the shared tier. The per-user LinkedIn loop is deliberately absent: it runs on
        # its owner's schedule with their credential, and cannot be reached from here.
        with timed("gather", report.timings):
            links = _gather(source_configs, planned, ats, run_source, report,
                            terms=original_terms(configs))
        report.links_found = len(links)

        # 4-5. Dedup and upsert.
        with timed("ingest", report.timings):
            report.new_jobs = _ingest(corpus, links, report)

        # 5b. Fetch the descriptions the listing APIs did not give us.
        #
        # **Before embedding, deliberately.** A vector computed from a 180-character teaser is a
        # vector of the advert, and every similarity read against it is wrong in a way nothing
        # downstream can detect. Describing first means the row embeds once, from the real text.
        #
        # Only the free tier here: LinkedIn needs a residential IP, so those rows are left for the
        # per-user pass that holds a lease. A shared cycle must never reach for a user's proxy.
        if describe:
            with timed("describe", report.timings), make_shared_client() as client:
                described = describe_pending(corpus, client, linkedin=False,
                                             limit=describe_limit)
            report.described = described.described
            report.describe_outcomes = described.outcomes

        # 6. Embed the new rows. Once, for everyone.
        if embedder is not None:
            with timed("embed", report.timings):
                result = embed_all_pending(corpus, embedder, expected=expected_embedder)
            report.embedded = result.embedded

        # 7. Ask the `score` lane to catch each account up.
        #
        # **Enqueued rather than done here.** Scoring needs a per-user model and the scorer's
        # fail-closed version checks, and doing it inline would couple harvesting to scoring: a
        # slow model load would delay the next harvest, and one account's mismatched artifact would
        # stall everybody's. Enqueued unconditionally — the worker decides whether there is
        # anything to score, and an account with no model yet ends its job quietly.
        #
        # Collapsed on `dedup_key`, so a cycle that finds nothing new does not pile up work.
        with timed("enqueue_scoring", report.timings):
            report.scoring_enqueued = _enqueue_scoring(session)

    logger.info(
        "harvest: %d queries (%d deferred), %d links, %d duplicates, %d new, %d described, "
        "%d embedded",
        report.queries_planned, report.queries_deferred, report.links_found,
        report.duplicates_total, report.new_jobs, report.described, report.embedded)
    report_timings(report.timings, event="harvest.timing", log=logger,
                   links=report.links_found, new=report.new_jobs)
    audit(logger, "harvest.cycle",
          queries=report.queries_planned, links=report.links_found,
          new=report.new_jobs, described=report.described, embedded=report.embedded,
          scoring_enqueued=report.scoring_enqueued,
          failed_sources=len(report.source_failures),
          empty_sources=sum(1 for n in report.links_by_source.values() if n == 0))
    return report, [q.key for q in deferred]


def _gather(source_configs: dict[str, dict], planned: list[PlannedQuery], ats: AtsRepository,
            run_source: Callable[..., list[JobLink]], report: CycleReport,
            *, terms: dict[str, str] | None = None) -> list[JobLink]:
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
    if not shared:
        # Every source disabled looks exactly like every source finding nothing.
        logger.warning("harvest: no shared sources are enabled — nothing will be fetched")

    def _config_for(source: Source) -> dict:
        cfg = dict(source_configs.get(source.key, {}))
        queries = queries_by_source.get(source.key)
        if queries:
            # **Rendered in the shape this adapter reads.** One fixed dict used to be handed to
            # every source; only two of the nine read `search_term`, and a query in the wrong shape
            # is silent — the board answers its unfiltered feed and reports success, which is
            # indistinguishable from a deliberately broad search.
            shape = QUERY_SHAPES[source.key]
            cfg["queries"] = [
                # **The phrase the user wrote, not the fold key.** `keyword_norm` sorts its tokens
                # and strips accents so two users asking the same thing collapse to one query;
                # typing that into a search box asks for `engineer stress` and truncates every
                # Hungarian term. The key stays the identity, the original goes to the board.
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
    """Collect a source's results, and say so when a source that was asked returns nothing.

    An adapter returning `[]` is the registry's contract for a dead source — which is what stops one
    broken source sinking a cycle, and is also what hid three sources making no HTTP request at all
    for a whole day. The contract stays; the silence does not.
    """
    links.extend(found)
    report.links_by_source[source.key] = len(found)
    if not found:
        logger.warning("source %s returned nothing — check it was actually asked for something",
                       source.key)


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


def _enqueue_scoring(session) -> int:
    """One `score` job per active user. Returns how many were newly queued.

    Every active account, not only those whose config matched something this cycle: a corpus row is
    shared, so a job harvested for one user is a job the others have not scored either. The dedup
    key collapses a repeat, so a cycle that finds nothing new costs one INSERT that does nothing.
    """
    queued = 0
    for user_id in session.scalars(select(User.id).where(User.active)):
        if enqueue(session, "score", user_id, dedup_key="score") is not None:
            queued += 1
    return queued
