"""One-shot harvest entrypoint."""
from __future__ import annotations

import argparse
import logging
import sys

from libs.db import database, ensure_schema
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
from services.harvester.linkedin import run_for_due_users as run_linkedin
from services.harvester.source_config import build_source_configs

logger = logging.getLogger("harvester")


def _configure_logging(verbose: bool) -> None:
    """One rotating file plus the console (`libs.obs`)."""
    configure("harvester", level="DEBUG" if verbose else "INFO")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one shared harvest cycle.")
    parser.add_argument("--dry-run", action="store_true",
                        help="build and budget the query set, then stop before fetching anything")
    parser.add_argument("--max-queries", type=int, default=None,
                        help="cap every source at this many queries for this run, ignoring the "
                             "operator's per-source limits")
    parser.add_argument("--linkedin", action="store_true",
                        help="run the per-user LinkedIn pass for whoever is due, then stop")
    parser.add_argument("--describe-linkedin", action="store_true",
                        help="fill LinkedIn descriptions on each user's own proxy, then stop")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)
    ensure_schema()

    if args.linkedin:
        # Per user, on their own schedule and their own credential.
        install()
        db = database()
        report = run_linkedin(db, limit=linkedin_concurrent_users(db))
        logger.info("linkedin: %d considered, %d ran, %d without a lease, %d link(s), %d new",
                    report.considered, report.ran, report.skipped_no_lease,
                    report.links_found, report.new_jobs)
        return 0

    if args.describe_linkedin:
        # A separate pass on a slower timer, not part of the cycle.
        install()
        db = database()
        totals = describe_linkedin_for_all(db)
        logger.info("describe(linkedin): %s", totals)
        return 0

    kwargs = {}
    if args.dry_run:
        kwargs["run_source"] = lambda *a, **k: []

    try:
        db = database()
        # Source pre-filters come from the union of active users' configs, never from one person's.
        source_configs = build_source_configs(
            TenantRepository(db).active_configs(),
            freshness={name: freshness_hours(db, name) for name in HARVEST_SOURCES},
            results={name: results_per_query(db, name) for name in HARVEST_SOURCES
                     if results_per_query(db, name)},
            pages={name: pages_per_cycle(db, name) for name in HARVEST_SOURCES
                   if pages_per_cycle(db, name)},
            # Sources that iterate their own demand rather than a planned query set read their
            # ceiling from the config; `select_within_budget` never sees them.
            queries={name: queries_per_cycle(db, name) for name in HARVEST_SOURCES},
            ats_companies=ats_companies_per_cycle(db))
        # The operator's per-source query ceilings, read where the db is.
        limits = (dict.fromkeys(HARVEST_SOURCES, args.max_queries)
                  if args.max_queries is not None
                  else {name: queries_per_cycle(db, name) for name in HARVEST_SOURCES})
        kwargs["limits"] = limits
        kwargs["describe_limit"] = describe_per_cycle(db)
        logger.info("harvest: %d sources enabled", len(source_configs))

        report, deferred = run_cycle(db, source_configs=source_configs, **kwargs)
    except Exception:
        logger.exception("harvest cycle failed")
        return 1

    if report.skipped_locked:
        # Not a failure: a 15-minute timer meeting an occasionally slower cycle is the design.
        logger.info("skipped — another cycle holds the lock")
        return 0

    logger.info(
        "cycle complete: %d queries planned, %d deferred, %d links, %d duplicates, %d new, "
        "%d source failures",
        report.queries_planned, len(deferred), report.links_found, report.duplicates_total,
        report.new_jobs, len(report.source_failures))
    for key, error in report.source_failures.items():
        logger.warning("source %s failed: %s", key, error)

    # The LinkedIn tier runs on the same tick, not its own timer.
    if not args.dry_run:
        _run_linkedin_pass()
    return 0


def _run_linkedin_pass() -> None:
    """The per-user LinkedIn tier."""
    try:
        db = database()
        report = run_linkedin(db, limit=linkedin_concurrent_users(db))
        logger.info("linkedin: %d considered, %d ran, %d without a lease, %d link(s), %d new",
                    report.considered, report.ran, report.skipped_no_lease,
                    report.links_found, report.new_jobs)
    except Exception:
        logger.exception("the LinkedIn pass failed; the shared harvest was unaffected")


if __name__ == "__main__":
    sys.exit(main())
