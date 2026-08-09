"""One harvest cycle, start to finish."""
from __future__ import annotations

import logging
import uuid as _uuid
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from time import monotonic

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from libs.contracts.embedder import EmbedderClient, EmbedderError, EmbedderVersion
from libs.corpus.ats import AtsRepository
from libs.corpus.repository import CorpusRepository
from libs.db.models import User
from libs.obs import Timings, audit, report_timings, timed
from libs.queue import enqueue
from libs.settings import HARVEST_CYCLE_BUDGET_SECONDS
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

# One arbitrary constant, shared by every harvester process.
HARVEST_LOCK_KEY = 0x6A6F_6273_0001

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
    embedded: int = 0
    # Set when the embedder refused or timed out.
    embed_error: str | None = None
    # Rows whose language was read from text that was already stored.
    languages_read: int = 0
    # Per source, so "the cycle found 200 links" cannot hide "and four of six sources found none".
    links_by_source: dict[str, int] = field(default_factory=dict)
    source_failures: dict[str, str] = field(default_factory=dict)
    # How many accounts were asked to catch up their scores.
    scoring_enqueued: int = 0
    labelling_enqueued: int = 0
    skipped_locked: bool = False
    # How long each stage took.
    timings: Timings = field(default_factory=Timings)

    @property
    def duplicates_total(self) -> int:
        return sum(self.duplicates.values())


@contextmanager
def harvest_lock(session: Session, *, key: int = HARVEST_LOCK_KEY):
    """Hold the cycle lock, or yield False if another run already has it."""
    acquired = bool(session.execute(
        text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar())
    try:
        yield acquired
    finally:
        if acquired:
            try:
                session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            except Exception:
                # Never let cleanup mask the failure that caused it.
                logger.debug("advisory unlock failed; the connection close will release it",
                             exc_info=True)


def _run_source(source: Source, cfg: dict, repo: AtsRepository) -> list[JobLink]:
    """Invoke one adapter."""
    kwargs = {"repo": repo} if source.needs_ats_repo else {}
    return source.adapter(cfg, logger, **kwargs)



# The pseudo-account operator keywords are folded in under.
OPERATOR_DEMAND_ID = _uuid.UUID("00000000-0000-0000-0000-0000000000ff")


def _operator_demand(session) -> dict:
    """Operator keywords, shaped like a user config so the fold needs no special case."""
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
    """Run one shared harvest cycle."""
    # The cycle's own clock, so a slow stage stops rather than taking the stages behind it with it.
    deadline = monotonic() + HARVEST_CYCLE_BUDGET_SECONDS
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

        # 1-2. Every active account's config, which is what the query set is folded from.
        configs = tenant.active_configs()

        # Operator-injected demand, folded in as one more participant.
        configs = {**configs, **_operator_demand(session)}
        # The sources that are actually enabled and actually read a query set.
        askable = tuple(sorted(QUERY_TAKING_SOURCES & set(source_configs)))
        # No location axis at all, decided 2026-08-04.
        query_set = build_query_set(configs, sources=askable,
                                    locating_sources=(), remote_aware_sources=(),
                                    budgets=_keyword_budgets(session, configs))
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

        # 5c. Read the language off whatever text now exists. No network, and once per posting.
        with timed("language", report.timings):
            report.languages_read = corpus.fill_missing_languages()

        # 6. Embed the new rows. Once, for everyone.
        if embedder is not None:
            try:
                with timed("embed", report.timings):
                    result = embed_all_pending(corpus, embedder, expected=expected_embedder,
                                               deadline=deadline)
                report.embedded = result.embedded
            except EmbedderError as exc:
                report.embed_error = str(exc)
                logger.error("harvest: embedding failed — those rows stay unembedded and are "
                             "retried next cycle: %s", exc)

        # 7. Ask the `score` and `label` lanes to catch each account up.
        with timed("enqueue_scoring", report.timings):
            report.scoring_enqueued = _enqueue_scoring(session)
        with timed("enqueue_labelling", report.timings):
            report.labelling_enqueued = _enqueue_labelling(session)

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
          labelling_enqueued=report.labelling_enqueued,
          failed_sources=len(report.source_failures),
          empty_sources=sum(1 for n in report.links_by_source.values() if n == 0))
    return report, [q.key for q in deferred]


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
            corpus.touch_last_seen([duplicate.job_id])
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


def _keyword_budgets(session, configs) -> dict:
    """Each account's own `keywords` ceiling, so the panel's value is the one that harvests."""
    from libs import limits  # noqa: PLC0415 - one caller, kept local

    out = {}
    for user_id in configs:
        user = session.get(User, user_id)
        # Operator-injected demand has no user row and keeps the catalogue default.
        out[user_id] = limits.resolve(user, "keywords") if user is not None else None
    return out


def _enqueue_scoring(session) -> int:
    """One `score` job per active user."""
    queued = 0
    for user_id in session.scalars(select(User.id).where(User.active)):
        if enqueue(session, "score", user_id, dedup_key="score") is not None:
            queued += 1
    return queued


def _enqueue_labelling(session) -> int:
    """One `label` job per active account that still owes itself labels.

    **The label lane had no way back in.** It was a chain that armed itself and nothing else: finish
    setup, and it ran until a batch reported nothing left. Any interruption — an empty pool, a
    rate limit, a `Terminal` error, a restart — ended it permanently, with no error raised and a
    model that looked trained. One account sat six days at 243 of its 300 labels that way, and the
    only reason it was found was somebody reading the queue table by hand.

    So the lane gets its work the way `score` does: off the harvest tick, dedup-keyed, idempotent.
    No new timer — a second clock is a second outage — and no cost when there is nothing to do,
    because a batch that writes nothing does not re-arm and `labelling_paused` is honoured here as
    well as at re-arm time.
    """
    from libs.labels import labelling_state  # noqa: PLC0415 - one caller, kept local

    queued = 0
    for user_id, paused in session.execute(
            select(User.id, User.labelling_paused).where(User.active)):
        if paused or labelling_state(session, user_id)["action"] == "train":
            continue
        if enqueue(session, "label", user_id, dedup_key="label") is not None:
            queued += 1
    return queued
