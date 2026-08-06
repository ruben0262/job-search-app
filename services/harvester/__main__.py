"""One-shot harvest entrypoint."""
from __future__ import annotations

import argparse
import logging
import os
import sys

from sqlalchemy.exc import OperationalError

from libs.contracts.embedder import EmbedderClient, EmbedderError, EmbedderVersion
from libs.corpus.repository import CorpusRepository
from libs.db.session import session_scope
from libs.obs import configure
from libs.secrets import install
from libs.settings import (
    HARVEST_SOURCES,
    ats_companies_per_cycle,
    describe_per_cycle,
    freshness_hours,
    linkedin_concurrent_users,
    pages_per_cycle,
    queries_per_cycle,
    results_per_query,
)
from libs.tenant.repository import TenantRepository
from services.harvester.cycle import run_cycle
from services.harvester.describe import describe_linkedin_for_all
from services.harvester.embedding import embed_pending
from services.harvester.linkedin import run_for_due_users as run_linkedin
from services.harvester.source_config import build_source_configs

logger = logging.getLogger("harvester")

# Consecutive failed batches before `--embed-only` stops.
MAX_EMBED_BATCH_FAILURES = 5

# Smaller than the harvest batch, on purpose.
EMBED_ONLY_BATCH = 64

# A bulk embed is not a web request.
EMBED_ONLY_TIMEOUT = 900.0


def _configure_logging(verbose: bool) -> None:
    """One rotating file plus the console (`libs.obs`)."""
    configure("harvester", level="DEBUG" if verbose else "INFO")


def _expected_embedder() -> EmbedderVersion | None:
    """The vector space the platform's trained artifacts assume, if it has been pinned."""
    model = os.environ.get("EMBEDDER_MODEL")
    dim = os.environ.get("EMBEDDER_DIM")
    if not model or not dim:
        return None
    return EmbedderVersion(model=model, dim=int(dim), version=os.environ.get("EMBEDDER_VERSION", ""))


def _embed_only() -> int:
    """Rebuild vectors until there are none left, and do nothing else."""
    embedder_url = os.environ.get("EMBEDDER_URL")
    if not embedder_url:
        logger.error("EMBEDDER_URL is not set — nothing can be embedded")
        return 2

    embedder = EmbedderClient(base_url=embedder_url, timeout=EMBED_ONLY_TIMEOUT)
    total = 0
    consecutive_failures = 0
    try:
        while True:
            # One batch per transaction, deliberately.
            try:
                with session_scope() as session:
                    result = embed_pending(CorpusRepository(session), embedder,
                                           expected=_expected_embedder(),
                                           batch_size=EMBED_ONLY_BATCH)
            except (OperationalError, EmbedderError) as exc:
                # Both of these are expected here, and neither may end the run.
                consecutive_failures += 1
                logger.warning("embed-only: batch failed (%s), retrying — %d in a row",
                               type(exc).__name__, consecutive_failures)
                if consecutive_failures >= MAX_EMBED_BATCH_FAILURES:
                    logger.error("embed-only: %d batches failed in a row, giving up with %d "
                                 "embedded", consecutive_failures, total)
                    return 1
                continue

            consecutive_failures = 0
            total += result.embedded
            logger.info("embed-only: %d embedded (%d superseded), %d total, %d remaining",
                        result.embedded, result.skipped_stale, total, result.remaining)
            if not result.did_work:
                return 0
    finally:
        embedder.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one shared harvest cycle.")
    parser.add_argument("--dry-run", action="store_true",
                        help="build and budget the query set, then stop before fetching anything")
    parser.add_argument("--max-queries", type=int, default=None,
                        help="cap every source at this many queries for this run, ignoring the "
                             "operator's per-source limits")
    parser.add_argument("--embed-only", action="store_true",
                        help="embed the backlog and nothing else, then stop")
    parser.add_argument("--linkedin", action="store_true",
                        help="run the per-user LinkedIn pass for whoever is due, then stop")
    parser.add_argument("--describe-linkedin", action="store_true",
                        help="fill LinkedIn descriptions on each user's own proxy, then stop")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)

    if args.embed_only:
        install()
        return _embed_only()

    if args.linkedin:
        # Per user, on their own schedule and their own credential.
        install()
        with session_scope() as session:
            report = run_linkedin(session, limit=linkedin_concurrent_users(session))
        logger.info("linkedin: %d considered, %d ran, %d without a lease, %d link(s), %d new",
                    report.considered, report.ran, report.skipped_no_lease,
                    report.links_found, report.new_jobs)
        return 0

    if args.describe_linkedin:
        # A separate pass on a slower timer, not part of the cycle.
        install()
        with session_scope() as session:
            totals = describe_linkedin_for_all(session)
        logger.info("describe(linkedin): %s", totals)
        return 0

    embedder_url = os.environ.get("EMBEDDER_URL")
    if not embedder_url and not args.dry_run:
        # Fail closed rather than harvesting rows nothing can embed.
        logger.error("EMBEDDER_URL is not set — refusing to harvest rows that cannot be embedded")
        return 2

    kwargs = {}
    if args.dry_run:
        kwargs["run_source"] = lambda *a, **k: []

    try:
        with session_scope() as session:
            # Source pre-filters come from the union of active users' configs, never from one person's.
            source_configs = build_source_configs(
                TenantRepository(session).active_configs(),
                freshness={name: freshness_hours(session, name) for name in HARVEST_SOURCES},
                results={name: results_per_query(session, name) for name in HARVEST_SOURCES
                         if results_per_query(session, name)},
                pages={name: pages_per_cycle(session, name) for name in HARVEST_SOURCES
                       if pages_per_cycle(session, name)},
                ats_companies=ats_companies_per_cycle(session))
            # The operator's per-source query ceilings, read where the session is.
            limits = (dict.fromkeys(HARVEST_SOURCES, args.max_queries)
                      if args.max_queries is not None
                      else {name: queries_per_cycle(session, name) for name in HARVEST_SOURCES})
            kwargs["limits"] = limits
            kwargs["describe_limit"] = describe_per_cycle(session)
            logger.info("harvest: %d sources enabled", len(source_configs))

            embedder = None if args.dry_run else EmbedderClient(base_url=embedder_url)
            try:
                report, deferred = run_cycle(
                    session, embedder=embedder, expected_embedder=_expected_embedder(),
                    source_configs=source_configs, **kwargs)
            finally:
                if embedder is not None:
                    embedder.close()
    except Exception:
        logger.exception("harvest cycle failed")
        return 1

    if report.skipped_locked:
        # Not a failure: a 15-minute timer meeting an occasionally slower cycle is the design.
        logger.info("skipped — another cycle holds the lock")
        return 0

    logger.info(
        "cycle complete: %d queries planned, %d deferred, %d links, %d duplicates, %d new, "
        "%d embedded, %d source failures",
        report.queries_planned, len(deferred), report.links_found, report.duplicates_total,
        report.new_jobs, report.embedded, len(report.source_failures))
    for key, error in report.source_failures.items():
        logger.warning("source %s failed: %s", key, error)

    # The LinkedIn tier runs on the same tick, not its own timer.
    if not args.dry_run:
        _run_linkedin_pass()
    return 0


def _run_linkedin_pass() -> None:
    """The per-user LinkedIn tier."""
    try:
        with session_scope() as session:
            report = run_linkedin(session, limit=linkedin_concurrent_users(session))
        logger.info("linkedin: %d considered, %d ran, %d without a lease, %d link(s), %d new",
                    report.considered, report.ran, report.skipped_no_lease,
                    report.links_found, report.new_jobs)
    except Exception:
        logger.exception("the LinkedIn pass failed; the shared harvest was unaffected")


if __name__ == "__main__":
    sys.exit(main())
