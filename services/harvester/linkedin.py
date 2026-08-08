"""The per-user LinkedIn pass."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import or_, select

from libs import limits
from libs.contracts.config import location_search_name
from libs.corpus.repository import CorpusRepository
from libs.db.models import User, UserConfig
from libs.obs import audit
from libs.secrets import SecretStore, redacting
from libs.secrets.keyring import SecretsError
from libs.settings import (
    PLATFORM_TICK_MIN,
    freshness_hours,
    queries_per_cycle,
    results_per_query,
)
from libs.settings import get as settings_get
from services.harvester.describe import describe_pending
from services.harvester.fetch import make_client
from services.harvester.governor import (
    LINKEDIN_MAX_CONCURRENT_USERS,
    LINKEDIN_MIN_INTERVAL_MIN,
    linkedin_enabled,
    next_linkedin_run,
    resolve_linkedin_interval,
)
from services.harvester.proxy_usage import ProxyCapExceeded, ProxyUsageRepository
from services.harvester.query_set import build_query_set, original_terms
from services.harvester.sources import jobspy_source

logger = logging.getLogger(__name__)

# Postings per query.
RESULTS_WANTED = 30

# What "unlimited descriptions per run" means to a query that needs a number.
UNLIMITED_DESCRIBES = 10_000

# How long one user's describe pass may run, derived from the timer that starts it.
DESCRIBE_BUDGET_SECONDS = PLATFORM_TICK_MIN * 60 * 2 / 3


@dataclass
class LinkedInReport:
    """What one pass did."""

    considered: int = 0
    ran: int = 0
    skipped_no_lease: int = 0
    links_found: int = 0
    new_jobs: int = 0
    described: int = 0
    failures: dict[str, str] = field(default_factory=dict)


def due_users(session, *, now: datetime, limit: int) -> list[User]:
    """Active users whose LinkedIn run is due, soonest first."""
    return list(session.scalars(
        select(User)
        .where(User.active.is_(True),
               User.status == "active",
               or_(User.linkedin_next_run_at.is_(None), User.linkedin_next_run_at <= now))
        .order_by(User.linkedin_next_run_at.asc().nulls_first())
        .limit(limit)))


def queries_for(session, user: User) -> list[dict]:
    """This user's own LinkedIn queries — their keywords, nobody else's."""
    config = session.scalar(select(UserConfig.config).where(UserConfig.user_id == user.id))
    if not config:
        return []

    keys = build_query_set({user.id: config}, sources=("linkedin",))
    terms = original_terms({user.id: config})
    queries = [{"search_term": terms.get(key.keyword_norm, key.keyword_norm),
                # The board wants a place name, not the slug we store.
                "location": location_search_name(key.location_norm) or None,
                "is_remote": key.is_remote, "hours_old": key.max_age_hours}
               for key in keys]
    return queries[:queries_per_cycle(session, "linkedin")]


def run_for_due_users(session, *, now: datetime | None = None,
                      limit: int = LINKEDIN_MAX_CONCURRENT_USERS,
                      gather=jobspy_source.gather_linkedin) -> LinkedInReport:
    """One pass over the users whose LinkedIn run is due."""
    report = LinkedInReport()
    if not linkedin_enabled():
        logger.warning("linkedin: tier disabled by HARVEST_LINKEDIN_DISABLED")
        return report

    now = now or datetime.now(UTC)
    corpus = CorpusRepository(session)
    usage = ProxyUsageRepository(session)
    # Read once per pass rather than per user: both are platform settings, not per-account ones.
    window = freshness_hours(session, "linkedin")
    depth = results_per_query(session, "linkedin") or RESULTS_WANTED
    # **Not `window`.** `freshness_hours` is how far back to *search*; `job_max_age_hours` is how
    # old a posting may be and still reach anybody. Describing is bounded by the second, because a
    # row past it has already been refused by the pre-score filter and its text will never be read.
    deliverable_for = int(settings_get(session, "job_max_age_hours"))
    store = SecretStore(session)

    for user in due_users(session, now=now, limit=limit):
        report.considered += 1
        # Scheduled first, deliberately: every path below wants the same next run, success or not.
        chosen = limits.resolve(user, "linkedin_interval_min")
        if chosen is None:
            chosen = user.linkedin_interval_min or LINKEDIN_MIN_INTERVAL_MIN
        interval = chosen
        user.linkedin_next_run_at = next_linkedin_run(now, resolve_linkedin_interval(interval))

        queries = queries_for(session, user)
        if not queries:
            continue

        try:
            ran = _run_one(user, queries, corpus=corpus, usage=usage, store=store,
                           report=report, gather=gather, hours_old=window,
                           results_wanted=depth, deliverable_for=deliverable_for)
        except Exception as exc:                                    # noqa: BLE001
            # One user's LinkedIn failing is not the tier failing.
            report.failures[str(user.id)] = f"{type(exc).__name__}: {exc}"
            logger.warning("linkedin: user %s failed: %s", user.id, type(exc).__name__)
            continue
        if ran:
            report.ran += 1

    audit(logger, "linkedin.pass", considered=report.considered, ran=report.ran,
          no_lease=report.skipped_no_lease, links=report.links_found, new=report.new_jobs,
          failures=len(report.failures))
    return report


def _run_one(user: User, queries: list[dict], *, corpus: CorpusRepository,
             usage: ProxyUsageRepository, store: SecretStore,
             report: LinkedInReport, gather, hours_old: int,
             results_wanted: int, deliverable_for: int | None = None) -> bool:
    """One user's fetch, inside their own credential's scope."""
    try:
        with store.use(user.id, "proxy") as secret:
            if secret is None:
                # Not a transient skip and not the same as the others.
                logger.warning(
                    "linkedin: %s has no proxy credential — their LinkedIn tier cannot run at all "
                    "and none of its jobs will ever reach them", user.id)
                report.skipped_no_lease += 1
                return False

            proxy = secret.reveal()
            with redacting(proxy):
                try:
                    lease = usage.acquire_lease(user.id, secret_resolver=lambda _ref: proxy)
                except ProxyCapExceeded:
                    logger.warning("linkedin: %s is at its daily proxy cap — resumes tomorrow",
                                   user.id)
                    report.skipped_no_lease += 1
                    return False
                if lease is None:
                    logger.warning("linkedin: %s could not take a proxy lease — the credential is "
                                   "quarantined or unusable", user.id)
                    report.skipped_no_lease += 1
                    return False

                cfg = {"queries": queries,
                       # The operator's ceiling, not the constant beside it.
                       "results_wanted": results_wanted,
                       # The operator's window, not the user's.
                       "hours_old": hours_old,
                       # Never from jobspy.
                       "linkedin_fetch_description": False}
                links = gather(cfg, logger, lease=lease)

                # Dedup, then describe what is left — on this same lease, in this same pass.
                new_jobs = _ingest(corpus, links, user_id=user.id)
                with make_client(lease) as client:
                    # `None` is Unlimited.
                    per_run = limits.resolve(user, "describe_per_run")
                    described = describe_pending(
                        corpus, client, linkedin=True,
                        limit=UNLIMITED_DESCRIBES if per_run is None else per_run,
                        budget_seconds=DESCRIBE_BUDGET_SECONDS,
                        max_age_hours=deliverable_for)

    except SecretsError:
        # The store could not answer. Not this user's fault and not a reason to stop the pass.
        report.skipped_no_lease += 1
        return False

    report.links_found += len(links)
    report.new_jobs += new_jobs
    report.described += described.described
    logger.info("linkedin: %s ran %d quer(ies), %d link(s), %d new, %d described",
                user.id, len(queries), len(links), new_jobs, described.described)
    return True


def _ingest(corpus: CorpusRepository, links, *, user_id=None) -> int:
    """Dedup and upsert, the same four layers the shared cycle applies.

    `user_id` is recorded on every row this pass creates. LinkedIn is the per-user tier, so unlike
    the shared cycle there is exactly one account that paid for the fetch, and saying which one
    turns "why is this posting in the corpus" from an inference into a query.
    """
    new = 0
    for link in links:
        if not link.url:
            continue
        if corpus.find_duplicate(link.url, link.title, link.company, link.location,
                                 application_url=link.application_url) is not None:
            continue
        job_id = corpus.add(**link.as_corpus_row(), discovered_by=user_id)
        if job_id is None:
            continue
        new += 1
        # The snippet is not a description and is not stored as one.
        if link.application_url:
            corpus.set_application_url(job_id, link.application_url)
    return new
