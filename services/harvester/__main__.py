"""One-shot harvest entrypoint. An external timer runs this; it does one cycle and exits.

    python -m services.harvester                 # one shared cycle
    python -m services.harvester --dry-run       # plan the queries, fetch nothing

**Exit codes are the interface**, because that is what a timer reports on:

    0  a cycle ran
    0  another cycle held the lock and this tick skipped  (not a failure - it is the design)
    1  the cycle raised
    2  configuration is missing or wrong

A skipped tick is deliberately not an error. With a 15-minute timer and an occasionally slow cycle,
treating overlap as failure would page somebody about the system working correctly.

Scheduling is external — systemd timer, cron, or the Phase 2 queue. `deploy/README.md` has the unit.
When `libs/queue` lands, `run_cycle` becomes the body of a queue worker and this file stays as the
manual-run path, which is worth keeping: "run one now and show me" is the first thing anybody wants
during an incident.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

from libs.contracts.embedder import EmbedderClient, EmbedderVersion
from libs.corpus.repository import CorpusRepository
from libs.db.session import session_scope
from libs.obs import configure
from libs.secrets import install
from libs.tenant.repository import TenantRepository
from services.harvester.cycle import run_cycle
from services.harvester.describe import describe_linkedin_for_all
from services.harvester.embedding import embed_all_pending
from services.harvester.linkedin import run_for_due_users as run_linkedin
from services.harvester.source_config import build_source_configs

logger = logging.getLogger("harvester")


def _configure_logging(verbose: bool) -> None:
    """One rotating file plus the console (`libs.obs`).

    A one-shot run under a systemd timer leaves nothing behind unless it is written down: the
    process exits, the container is removed, and the only evidence a cycle happened at all is the
    log file.
    """
    configure("harvester", level="DEBUG" if verbose else "INFO")


def _expected_embedder() -> EmbedderVersion | None:
    """The vector space the platform's trained artifacts assume, if it has been pinned.

    Unset is correct only before any classifier exists. Once one does, a mismatch must be a refusal
    rather than a silently different vector space, so this is read from the environment and passed
    into the cycle — see `libs/contracts/embedder.py`.
    """
    model = os.environ.get("EMBEDDER_MODEL")
    dim = os.environ.get("EMBEDDER_DIM")
    if not model or not dim:
        return None
    return EmbedderVersion(model=model, dim=int(dim), version=os.environ.get("EMBEDDER_VERSION", ""))


def _embed_only() -> int:
    """Rebuild vectors until there are none left, and do nothing else.

    **No gather, no describe, and no advisory lock.** A full cycle spends most of a minute on HTTP
    before it reaches the embed step and holds the cycle lock throughout, so draining a re-embed
    backlog by running cycles is both slow and self-blocking — eight of them in a row rebuilt
    nothing at all, because each was locked out by the one before.
    """
    embedder_url = os.environ.get("EMBEDDER_URL")
    if not embedder_url:
        logger.error("EMBEDDER_URL is not set — nothing can be embedded")
        return 2

    embedder = EmbedderClient(base_url=embedder_url)
    total = 0
    try:
        while True:
            with session_scope() as session:
                result = embed_all_pending(CorpusRepository(session), embedder,
                                           expected=_expected_embedder())
            total += result.embedded
            logger.info("embed-only: %d this batch, %d total, %d remaining",
                        result.embedded, total, result.remaining)
            if not result.embedded:
                return 0
    finally:
        embedder.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one shared harvest cycle.")
    parser.add_argument("--dry-run", action="store_true",
                        help="build and budget the query set, then stop before fetching anything")
    parser.add_argument("--max-queries", type=int, default=None,
                        help="override the fan-out budget for this run")
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
        # **Per user, on their own schedule and their own credential.** Separate from the shared
        # cycle because the cadence, the bandwidth and the ban risk all belong to one account
        # rather than to the platform (`services/harvester/linkedin.py`).
        install()
        with session_scope() as session:
            report = run_linkedin(session)
        logger.info("linkedin: %d considered, %d ran, %d without a lease, %d link(s), %d new",
                    report.considered, report.ran, report.skipped_no_lease,
                    report.links_found, report.new_jobs)
        return 0

    if args.describe_linkedin:
        # **A separate pass on a slower timer, not part of the cycle.** It spends users' own
        # bandwidth rather than the platform's, so it is bounded per user and runs hourly — where
        # the shared cycle runs every fifteen minutes because it costs nothing.
        install()
        with session_scope() as session:
            totals = describe_linkedin_for_all(session)
        logger.info("describe(linkedin): %s", totals)
        return 0

    embedder_url = os.environ.get("EMBEDDER_URL")
    if not embedder_url and not args.dry_run:
        # Fail closed rather than harvesting rows nothing can embed. They would sit unembedded and
        # unscoreable, and the cycle would report success.
        logger.error("EMBEDDER_URL is not set — refusing to harvest rows that cannot be embedded")
        return 2

    kwargs = {}
    if args.max_queries is not None:
        kwargs["max_queries"] = args.max_queries
    if args.dry_run:
        kwargs["run_source"] = lambda *a, **k: []

    try:
        with session_scope() as session:
            # Source pre-filters are derived from the union of ACTIVE users' configs, never from
            # one person's keywords - the corpus is shared, so filtering it to one user would
            # silently deny everyone else the jobs they asked for.
            source_configs = build_source_configs(TenantRepository(session).active_configs())
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
