"""Fill in the descriptions the listing APIs did not give us."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from time import monotonic

from libs.corpus.postings import ProxyUnusable, fetch_job, fetch_meta
from libs.corpus.repository import DESCRIBE_MAX_FAILURES, CorpusRepository
from libs.db.models import User
from libs.obs import audit
from libs.secrets import SecretStore, redacting
from services.harvester.fetch import ProxyCapExceeded, make_client
from services.harvester.proxy_usage import ProxyUsageRepository

logger = logging.getLogger(__name__)

# How many rows one pass will fetch.
SHARED_LIMIT = 200
PROXIED_LIMIT = 60


@dataclass
class DescribeReport:
    """What one pass did, and why it failed where it did."""

    attempted: int = 0
    described: int = 0
    still_thin: int = 0
    metadata_written: int = 0
    outcomes: dict[str, int] = field(default_factory=dict)
    # Set when the pass stopped because the proxy refused: out of traffic, bad credentials, down.
    proxy_error: str | None = None
    # Postings that answered "no longer accepting applications".
    closed: int = 0
    # Postings given up on after DESCRIBE_MAX_FAILURES consecutive unreadable fetches. The number
    # worth watching: it is proxy spend the platform will never make again.
    abandoned: int = 0

    @property
    def hit_rate(self) -> float:
        return self.described / self.attempted if self.attempted else 0.0


def describe_pending(corpus: CorpusRepository, client, *, linkedin: bool | None = None,
                     limit: int = SHARED_LIMIT,
                     budget_seconds: float | None = None,
                     max_age_hours: int | None = None) -> DescribeReport:
    """Fetch and store descriptions for corpus rows that are still thin.

    `max_age_hours` is the operator's delivery window. A posting past it cannot reach anybody, so
    fetching its text is bandwidth spent on a row the pre-score filter has already refused.
    """
    report = DescribeReport()
    jobs = corpus.awaiting_description(limit=limit, linkedin=linkedin,
                                       max_age_hours=max_age_hours)
    deadline = None if budget_seconds is None else monotonic() + budget_seconds

    for job in jobs:
        if deadline is not None and monotonic() >= deadline:
            logger.info("describe: stopping at %d of %d, the pass is out of time — the rest are "
                        "first in line next run", report.attempted, len(jobs))
            break
        report.attempted += 1
        try:
            posting = fetch_job(job.url, client=client, snippet=job.description or "",
                                title=job.title or "", stats=report.outcomes)
        except ProxyUnusable as exc:
            # The credential is the problem, not this job.
            report.attempted -= 1
            report.proxy_error = str(exc)
            logger.warning("describe: stopping, the proxy refused: %s", exc)
            break

        if posting.get("accepting") is False:
            # The one fetch we were always going to make is also the one that can tell us the
            # posting is dead — so this costs nothing and stops every later stage paying for it.
            corpus.mark_closed(job.id)
            report.closed += 1
            continue

        # Metadata is worth keeping even when the description came back thin: the card and filter read it.
        before = (job.applicants, job.easy_apply, job.reposted, job.workplace)
        corpus.set_posting_metadata(
            job.id, applicants=posting["applicants"],
            applicants_caption=posting.get("applicants_caption"),
            reposted=posting["reposted"],
            workplace=posting["workplace"], posted_at=posting["posted_at"])
        if (job.applicants, job.easy_apply, job.reposted, job.workplace) != before:
            report.metadata_written += 1

        if posting["application_url"] and not job.application_url:
            # Both the link the user needs at apply time and the strongest cross-source dedup key there is.
            corpus.set_application_url(job.id, posting["application_url"])

        text = posting.description
        if len(text) > len(job.description or ""):
            corpus.set_description(job.id, text)
            corpus.clear_describe_failures(job.id)
            report.described += 1
        else:
            # The page gave us nothing better than we already had. Counted on the row, because a
            # describer with no memory asks the same dead posting again every pass — on the
            # proxied tier, with the user's own metered bandwidth, forever.
            failures = corpus.record_describe_failure(job.id)
            report.still_thin += 1
            if failures >= DESCRIBE_MAX_FAILURES:
                report.abandoned += 1
                logger.warning("describe: giving up on %s after %d failed reads — it will not be "
                               "described, embedded or scored again", job.url, failures)

    logger.info("describe: %d attempted, %d described, %d still thin — %s",
                report.attempted, report.described, report.still_thin, report.outcomes)
    audit(logger, "harvest.describe", attempted=report.attempted, described=report.described,
          still_thin=report.still_thin, abandoned=report.abandoned, linkedin=linkedin,
          outcomes=report.outcomes)
    return report


def refresh_metadata(corpus: CorpusRepository, client, *, limit: int) -> DescribeReport:
    """Read the posting signals for LinkedIn rows that already have a description."""
    report = DescribeReport()
    for job in corpus.awaiting_metadata(limit=limit):
        report.attempted += 1
        try:
            meta = fetch_meta(job.url, client=client, stats=report.outcomes)
        except ProxyUnusable as exc:
            report.attempted -= 1
            report.proxy_error = str(exc)
            logger.warning("metadata: stopping, the proxy refused: %s", exc)
            break

        if meta["accepting"] is False:
            # The posting itself says so.
            corpus.mark_closed(job.id)
            report.closed += 1

        before = (job.applicants, job.easy_apply, job.reposted, job.workplace, job.posted_at)
        corpus.set_posting_metadata(
            job.id, applicants=meta["applicants"], easy_apply=meta["easy_apply"],
            reposted=meta["reposted"], workplace=meta["workplace"], posted_at=meta["posted_at"])
        if (job.applicants, job.easy_apply, job.reposted, job.workplace,
                job.posted_at) != before:
            report.metadata_written += 1
        if meta["application_url"] and not job.application_url:
            corpus.set_application_url(job.id, meta["application_url"])

    logger.info("metadata: %d attempted, %d written, %d closed — %s",
                report.attempted, report.metadata_written, report.closed, report.outcomes)
    audit(logger, "harvest.metadata", attempted=report.attempted,
          written=report.metadata_written, outcomes=report.outcomes)
    return report


def describe_linkedin_for_all(db, *, limit_per_user: int = PROXIED_LIMIT) -> dict[str, int]:
    """The proxied half of the pass, run for every user who brought a proxy credential."""
    corpus = CorpusRepository(db)
    usage = ProxyUsageRepository(db)
    store = SecretStore(db)
    totals: dict[str, int] = {"users": 0, "attempted": 0, "described": 0}

    for user_id in db[User.COLLECTION].distinct("_id", {"active": True}):
        # The plaintext lives inside `store.use` and nowhere else, so it outlives no request.
        with store.use(user_id, "proxy") as secret:
            if secret is None:
                continue
            proxy_url = secret.reveal()
            with redacting(proxy_url):
                try:
                    lease = usage.acquire_lease(user_id, secret_resolver=lambda _ref, url=proxy_url: url)
                except ProxyCapExceeded:
                    continue
                if lease is None:
                    continue

                totals["users"] += 1
                with make_client(lease) as client:
                    report = describe_pending(corpus, client, linkedin=True,
                                              limit=limit_per_user)
                    # Descriptions first, then whatever budget is left on metadata.
                    remaining = max(limit_per_user - report.attempted, 0)
                    meta = (refresh_metadata(corpus, client, limit=remaining)
                            if remaining and not report.proxy_error else DescribeReport())
                    usage.record_usage(user_id, megabytes=client.megabytes_downloaded)
                report.proxy_error = report.proxy_error or meta.proxy_error
                totals["metadata"] = totals.get("metadata", 0) + meta.metadata_written
                if report.proxy_error:
                    # Recorded against the credential, not swallowed.
                    usage.record_failure(user_id, report.proxy_error[:200])
                    totals["proxy_failures"] = totals.get("proxy_failures", 0) + 1

        totals["attempted"] += report.attempted
        totals["described"] += report.described

    logger.info("describe(linkedin): %d user(s), %d attempted, %d described",
                totals["users"], totals["attempted"], totals["described"])
    return totals
