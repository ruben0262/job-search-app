"""The per-user LinkedIn pass. The one tier that runs on its owner's schedule and their credential.

**Everything this needs existed and nothing called it.** The adapter (`gather_linkedin`), the lease
(`ProxyUsageRepository.acquire_lease`), the governor (concurrency ceiling, interval floor, jitter,
kill switch) and the two scheduling columns on `users` were all built. What was missing was the
loop, and its absence is why a corpus of 3,495 LinkedIn rows all carried the same `first_seen_at`:
they came from one import and nothing has fetched a LinkedIn posting since.

That mattered more than it looks. LinkedIn produced 337 of the founder account's 348 jobs at or
above the delivery cutoff; the seven aggregator sources running in its place produced nine in two
days. "My feed is quiet" was an ingestion gap wearing a scoring gap's clothes.

## Why this is not part of the shared cycle

Three reasons, and each on its own would be enough:

- **It spends the user's bandwidth**, so its cadence is theirs — derived from how often they want
  to hear from us, floored at an hour by `LINKEDIN_MIN_INTERVAL_MIN`. A user on a four-hour digest
  does not need LinkedIn read ninety-six times a day.
- **It carries ban risk.** The shared cycle must not stop because LinkedIn started blocking one
  user's residential IP, and one account's quarantine must not touch anyone else's feed.
- **There is no cross-user dedup on this tier** (`HARVESTER_PLAN.md` §5.3). Two users searching the
  same terms both fetch, because the alternative is spending one user's credential on another's
  query, and the rows land in a shared corpus anyway so the second fetch is cheap in storage terms.

## Failure is per user, always

A user with no proxy credential, an exhausted cap, or a quarantined credential is skipped and
**still gets their next run scheduled**. Leaving `linkedin_next_run_at` unset on failure would
either re-attempt them on every tick or drop them from the rotation entirely, depending on the
query — and both are worse than waiting an interval.
"""
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
    DELIVERY_INTERVAL_MIN,
    freshness_hours,
    queries_per_cycle,
    results_per_query,
)
from services.harvester.describe import describe_pending
from services.harvester.fetch import make_client
from services.harvester.governor import (
    LINKEDIN_MAX_CONCURRENT_USERS,
    linkedin_enabled,
    next_linkedin_run,
    resolve_linkedin_interval,
)
from services.harvester.proxy_usage import ProxyCapExceeded, ProxyUsageRepository
from services.harvester.query_set import build_query_set, original_terms
from services.harvester.sources import jobspy_source

logger = logging.getLogger(__name__)

# Postings per query. LinkedIn paginates and every page is proxied bandwidth, so this is the
# reference's figure rather than a bigger number that would feel more thorough.
RESULTS_WANTED = 30

# What "unlimited descriptions per run" means to a query that needs a number. A pass is bounded by
# what it actually found, so this is a guard rather than a ceiling anybody reaches.
UNLIMITED_DESCRIBES = 10_000

# **How long one user's describe pass may run, derived from the timer that starts it.**
#
# `linkedin.timer` ticks every fifteen minutes and `Type=oneshot` means a run still going blocks the
# next: measured 2026-08-05, a pass twenty minutes in with the timer reporting no next run, and five
# hours without a single LinkedIn row while every other source stayed current. A count cannot bound
# this — one row is a proxied fetch whose cost varies by an order of magnitude — so the pass bounds
# itself in seconds and leaves the rest for next time (ROADMAP 6am).
#
# Two thirds of the tick, so several users can still be served in one pass and the run always ends
# before the timer wants to start another.
LINKEDIN_TICK_MINUTES = 15
DESCRIBE_BUDGET_SECONDS = LINKEDIN_TICK_MINUTES * 60 * 2 / 3


@dataclass
class LinkedInReport:
    """What one pass did. Per user, because a tier that fails for one account and works for the
    rest is the normal case here rather than an exception worth collapsing into a total."""

    considered: int = 0
    ran: int = 0
    skipped_no_lease: int = 0
    links_found: int = 0
    new_jobs: int = 0
    described: int = 0
    failures: dict[str, str] = field(default_factory=dict)


def due_users(session, *, now: datetime, limit: int) -> list[User]:
    """Active users whose LinkedIn run is due, soonest first.

    `NULL` counts as due: a user who has never run is exactly who should run next, and treating an
    unset column as "not yet" would leave every new account permanently unscheduled.
    """
    return list(session.scalars(
        select(User)
        .where(User.active.is_(True),
               User.status == "active",
               or_(User.linkedin_next_run_at.is_(None), User.linkedin_next_run_at <= now))
        .order_by(User.linkedin_next_run_at.asc().nulls_first())
        .limit(limit)))


def queries_for(session, user: User) -> list[dict]:
    """This user's own LinkedIn queries — their keywords, nobody else's.

    The shared cycle folds every active user's terms into one query set because the corpus it fills
    is shared. Here the opposite is true: the fetch is paid for by one person, so it asks only what
    that person asked for. Sending the folded set would spend their bandwidth on other users' jobs.

    **Keyword × location, and that is deliberate here** (2026-08-04). The shared cycle dropped the
    location axis because one query there serves every user, most boards cannot filter by place, and
    a posting's country is on the posting. **None of that holds on this tier.** It is per user, it
    runs on that user's own paid proxy, and LinkedIn does geo search properly — so a filter the
    board applies is free and one we apply afterwards is bandwidth already spent. Dropping it for a
    few hours put **384 of 1,165 rows in the United States** for an account that never asked for it.

    The ceiling this crosses into is real and was the reason it came off in the first place: 23
    keywords × 4 locations against a cap of 12 meant the alphabetically first three terms in four
    places and nothing else. The cap is a per-source operator setting now, sized to cover the cross
    product rather than to truncate it.

    The **phrase the user wrote**, not the fold key: `keyword_norm` sorts its tokens and strips
    accents, which is right for deciding whether two searches are the same and wrong for typing into
    a search box.
    """
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
    """One pass over the users whose LinkedIn run is due.

    `gather` is injectable so the orchestration — who is due, whose credential, what happens when a
    lease is refused, when the next run lands — is testable without jobspy or a network. What the
    adapter does with a query is covered structurally elsewhere.

    **Does not commit.** The caller owns the transaction, so a scheduled next run and the rows it
    produced land together.
    """
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
    store = SecretStore(session)

    for user in due_users(session, now=now, limit=limit):
        report.considered += 1
        # Scheduled first, and deliberately: every path below either succeeds or fails, and both
        # want the same next run. Setting it at the end would skip it on the exception path and
        # leave the user re-attempted on every tick.
        # **The operator's control, not the column beside it.** `linkedin_interval_min` is a limit
        # in the admin panel and it reached nothing: this read `users.linkedin_interval_min`, a
        # column the panel does not write, so moving the slider moved a number on a page. The column
        # remains the fallback for an account nobody has tuned.
        # `linkedin_interval_min` forbids unlimited, so `resolve` always answers with a number:
        # the operator's, or the catalogue default. The column is the legacy fallback for a row
        # written before the limit existed.
        chosen = limits.resolve(user, "linkedin_interval_min")
        if chosen is None:
            chosen = user.linkedin_interval_min or DELIVERY_INTERVAL_MIN
        interval = chosen
        user.linkedin_next_run_at = next_linkedin_run(now, resolve_linkedin_interval(interval))

        queries = queries_for(session, user)
        if not queries:
            continue

        try:
            ran = _run_one(user, queries, corpus=corpus, usage=usage, store=store,
                           report=report, gather=gather, hours_old=window,
                           results_wanted=depth)
        except Exception as exc:                                    # noqa: BLE001
            # One user's LinkedIn failing is not the tier failing. Recorded and moved past, which
            # is the same contract every shared source adapter has.
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
             results_wanted: int) -> bool:
    """One user's fetch, inside their own credential's scope. Returns whether it ran.

    The proxy URL lives only inside `store.use`, and the whole fetch happens within that block —
    jobspy is handed the value and the block closes before anything else runs.
    """
    try:
        with store.use(user.id, "proxy") as secret:
            if secret is None:
                # **Not a transient skip and not the same as the others.** Every other reason below
                # clears on its own — a cap resets at midnight, a quarantine lifts. This one is
                # permanent until the user saves a credential, and it silently costs them every
                # LinkedIn job there is. It was counted into `skipped_no_lease` and never said
                # (ROADMAP 6ao).
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
                       # The operator's ceiling, not the constant beside it. `source_results_per_query`
                       # carried a value for LinkedIn from the day it was added and this line read
                       # `RESULTS_WANTED` regardless — the same defect the setting was created to fix,
                       # reintroduced in the commit that fixed it.
                       "results_wanted": results_wanted,
                       # **The operator's window, not the user's.** This read the narrowest window
                       # any of the user's own sections asked for, so the per-source setting in the
                       # admin panel — which for LinkedIn is deliberately the tightest on the
                       # platform, because it lists within minutes and a wide window is mostly
                       # re-reading — reached everything except LinkedIn. Measured on the live box
                       # as 23-hour-old postings arriving into a 2-hour window.
                       "hours_old": hours_old,
                       # **Never from jobspy.** It would fetch a description for every result
                       # including the ones already in the corpus; the dedup below is what makes the
                       # per-posting cost land once per *new* job rather than once per result.
                       "linkedin_fetch_description": False}
                links = gather(cfg, logger, lease=lease)

                # **Dedup, then describe what is left — on this same lease, in this same pass.**
                # The listing is one request for a page of results; a description is one request
                # per posting, so asking jobspy for them (`linkedin_fetch_description`) would pay
                # for every result including the ones already held. Deduping first means the
                # per-posting cost is paid once per *new* job, by whoever finds it, and every other
                # user reads it from the shared corpus for nothing.
                #
                # It used to run on its own hourly timer, which left rows scored on a snippet for
                # up to an hour and then rescored underneath the user. There is no window now.
                new_jobs = _ingest(corpus, links)
                with make_client(lease) as client:
                    # `None` is Unlimited. Stated as a branch rather than an `or`, because `or`
                    # cannot tell "no ceiling" from "unset" and that ambiguity is what made an
                    # account set to Unlimited run at the default.
                    per_run = limits.resolve(user, "describe_per_run")
                    described = describe_pending(
                        corpus, client, linkedin=True,
                        limit=UNLIMITED_DESCRIBES if per_run is None else per_run,
                        budget_seconds=DESCRIBE_BUDGET_SECONDS)

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


def _ingest(corpus: CorpusRepository, links) -> int:
    """Dedup and upsert, the same four layers the shared cycle applies.

    Kept here rather than imported from `cycle` because that one also writes a `CycleReport`; the
    dedup itself is the repository's, so this is only the loop around it.
    """
    new = 0
    for link in links:
        if not link.url:
            continue
        if corpus.find_duplicate(link.url, link.title, link.company, link.location,
                                 application_url=link.application_url) is not None:
            continue
        job_id = corpus.add(**link.as_corpus_row())
        if job_id is None:
            continue
        new += 1
        # **The snippet is not a description and is not stored as one.** A row described by its
        # preview gets embedded and scored on a paragraph, and the full JD arriving later would
        # silently change that score — a job rejected on a preview is never re-examined by anyone.
        # A row with no description is simply not scored until the fetch below gives it one.
        if link.application_url:
            corpus.set_application_url(job_id, link.application_url)
    return new
