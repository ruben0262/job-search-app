"""One-shot harvest entrypoint."""
from __future__ import annotations

import argparse
import logging
import sys
from concurrent.futures import ThreadPoolExecutor

from libs.corpus.terms import catch_up
from libs.db import database, ensure_schema
from libs.obs import configure
from libs.secrets import install
from libs.settings import (
    HARVEST_SOURCES,
    ats_companies_per_cycle,
    describe_per_cycle,
    freshness_hours,
    pages_per_cycle,
    queries_per_cycle,
    results_per_query,
)
from libs.tenant.repository import TenantRepository
from services.harvester.cycle import harvest_lock, run_cycle
from services.harvester.linkedin import run_pass as run_linkedin
from services.harvester.source_config import build_source_configs

logger = logging.getLogger("harvester")


def _configure_logging(verbose: bool) -> None:
    """One rotating file plus the console (`libs.obs`)."""
    configure("harvester", level="DEBUG" if verbose else "INFO")


def source_configs_for(db) -> dict[str, dict]:
    """What every board is asked this cycle, from the union of active users' configs (never from one
    person's) and the operator's per-board settings. Also what `check_sources.py` asks them."""
    return build_source_configs(
        TenantRepository(db).active_configs(),
        freshness={name: freshness_hours(db, name) for name in HARVEST_SOURCES},
        results={name: results_per_query(db, name) for name in HARVEST_SOURCES
                 if results_per_query(db, name)},
        pages={name: pages_per_cycle(db, name) for name in HARVEST_SOURCES
               if pages_per_cycle(db, name)},
        # Sources that iterate their own demand rather than a planned query set read their ceiling
        # from the config; `select_within_budget` never sees them.
        queries={name: queries_per_cycle(db, name) for name in HARVEST_SOURCES},
        ats_companies=ats_companies_per_cycle(db))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one shared harvest cycle.")
    parser.add_argument("--dry-run", action="store_true",
                        help="build and budget the query set, then stop before fetching anything")
    parser.add_argument("--max-queries", type=int, default=None,
                        help="cap every source at this many queries for this run, ignoring the "
                             "operator's per-source limits")
    parser.add_argument("--linkedin", action="store_true",
                        help="run the LinkedIn pass alone, then stop")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)
    # Every path, the cycle's own LinkedIn pass included: the site proxy's address carries a password.
    install()
    ensure_schema()

    if args.linkedin:
        _run_linkedin_pass()
        _find_words()
        return 0

    with ThreadPoolExecutor(max_workers=1) as beside:
        # LinkedIn beside the boards, not after them: it goes out through the site proxy and they go direct,
        # so neither waits on the other and each has the tick's whole budget. The words come after both.
        if not args.dry_run:
            beside.submit(_run_linkedin_pass)
        code = _run_shared(args)
    if not args.dry_run:
        _find_words()
    return code


def _run_shared(args) -> int:
    """The shared cycle: every board but LinkedIn."""
    kwargs = {}
    if args.dry_run:
        kwargs["run_source"] = lambda *a, **k: []

    try:
        db = database()
        source_configs = source_configs_for(db)
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
    return 0


def _run_linkedin_pass() -> None:
    """The LinkedIn pass: every country somebody picked, every job in it stored and described."""
    try:
        report = run_linkedin(database())
        logger.info("linkedin: %d place(s), %d request(s), %d job(s) listed, %d new, %d described",
                    report.swept_places, report.sweep_requests, report.swept, report.new_jobs,
                    report.described)
    except Exception:
        logger.exception("the LinkedIn pass failed; the shared harvest was unaffected")


def _find_words() -> None:
    """Which of everyone's words the postings have (`libs.corpus.terms`), under the harvest's lock so one
    pass at a time writes them. A failure costs speed, not jobs: matching reads what is not found yet
    with a regex."""
    try:
        db = database()
        with harvest_lock(db) as acquired:
            if acquired:
                catch_up(db)
            else:
                logger.info("terms: another harvest holds the lock; its own pass will find the words")
    except Exception:
        logger.exception("finding the words in the postings failed; matching reads them the slow way")


if __name__ == "__main__":
    sys.exit(main())
