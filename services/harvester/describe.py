"""Fill in the descriptions the listing APIs did not give us.

**The step the platform was missing.** Harvest stored whatever `snippet` a source returned and
scored on it. For the Tier-2 API sources that is the whole posting and nothing is owed; for
LinkedIn and most boards it is a teaser, and the model was being asked to judge a job from its
advertisement. This pass takes the rows that came out thin and fetches the posting itself.

## Two passes, because two different people pay

- **The shared pass** runs in the harvest cycle on the unproxied client. Boards and ATS pages
  answer a datacenter IP happily, so this is free and benefits everyone at once.
- **The proxied pass** runs per user with their own `ProxyLease`. LinkedIn's guest endpoint blocks
  datacenter IPs, so those rows cannot be filled any other way — and the bandwidth is metered to
  whoever caused it, which is what stops one account's backlog being paid for by another's cap.

Both write to the same shared rows. That is the point: LinkedIn descriptions fetched on one user's
proxy make the corpus better for every user, and the second account to want that job pays nothing.

## Fail-soft, and loud about it

A fetch that fails leaves the row exactly as it was, so the next pass picks it up again. What is
*not* allowed is failing silently: the outcome tally comes back on the report, because a run that
fell back to snippets for every job looks identical from the outside to one that worked.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import select

from libs.corpus.postings import ProxyUnusable, fetch_job
from libs.corpus.repository import CorpusRepository
from libs.db.models import User
from libs.obs import audit
from libs.secrets import SecretStore, redacting
from services.harvester.fetch import ProxyCapExceeded, make_client
from services.harvester.proxy_usage import ProxyUsageRepository

logger = logging.getLogger(__name__)

# How many rows one pass will fetch. Bounded because each LinkedIn fetch costs a proxied request
# and up to six retries — an unbounded pass on a cold-start corpus would spend a user's monthly
# bandwidth in one cycle.
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
    # Set when the pass stopped because the proxy itself refused — out of traffic, bad credentials,
    # unreachable. A run that ends this way is not a run that found nothing.
    proxy_error: str | None = None

    @property
    def hit_rate(self) -> float:
        return self.described / self.attempted if self.attempted else 0.0


def describe_pending(corpus: CorpusRepository, client, *, linkedin: bool | None = None,
                     limit: int = SHARED_LIMIT) -> DescribeReport:
    """Fetch and store descriptions for corpus rows that are still thin.

    `client` is whatever the caller is authorised to spend — the shared client for the free tier,
    a `TenantClient` holding a lease for LinkedIn. This function does not build one, so no path
    through it can fetch on an IP nobody is accounting for.

    A row that comes back thin anyway is left thin rather than marked done. It will be retried on
    the next pass, which is correct for the common cause: LinkedIn returning 429 to a burst.
    """
    report = DescribeReport()
    jobs = corpus.awaiting_description(limit=limit, linkedin=linkedin)

    for job in jobs:
        report.attempted += 1
        try:
            posting = fetch_job(job.url, client=client, snippet=job.description or "",
                                title=job.title or "", stats=report.outcomes)
        except ProxyUnusable as exc:
            # The credential is the problem, not this job. Continuing would spend six retries per
            # row on a proxy that has already said no.
            report.attempted -= 1
            report.proxy_error = str(exc)
            logger.warning("describe: stopping, the proxy refused: %s", exc)
            break

        # The page answers several questions at once, and the metadata is worth keeping even when
        # the description came back thin — an applicant count and a workplace tag are what the card
        # and the filter read, and they arrive on responses that carried no usable JD.
        before = (job.applicants, job.easy_apply, job.reposted, job.workplace)
        corpus.set_posting_metadata(
            job.id, applicants=posting["applicants"], reposted=posting["reposted"],
            workplace=posting["workplace"], posted_at=posting["posted_at"])
        if (job.applicants, job.easy_apply, job.reposted, job.workplace) != before:
            report.metadata_written += 1

        if posting["application_url"] and not job.application_url:
            # Both the link the user needs at apply time and the strongest cross-source dedup key
            # there is — a board posting whose apply target is already tracked is the same job.
            corpus.set_application_url(job.id, posting["application_url"])

        text = posting.description
        if len(text) > len(job.description or ""):
            corpus.set_description(job.id, text)
            report.described += 1
        else:
            report.still_thin += 1

    logger.info("describe: %d attempted, %d described, %d still thin — %s",
                report.attempted, report.described, report.still_thin, report.outcomes)
    audit(logger, "harvest.describe", attempted=report.attempted, described=report.described,
          still_thin=report.still_thin, linkedin=linkedin, outcomes=report.outcomes)
    return report


def describe_linkedin_for_all(session, *, limit_per_user: int = PROXIED_LIMIT) -> dict[str, int]:
    """The proxied half of the pass, run for every user who brought a proxy credential.

    **LinkedIn rows are the ones that need this most and can least be got for free.** Its guest
    endpoint refuses datacenter addresses, so the shared cycle cannot touch them: on this platform
    that left every LinkedIn posting in the corpus with no description beyond its title, and no
    applicant count, posted time or workplace tag at all — three of the five things ranking reads.

    Each user's own lease pays for their own requests, and the rows they fill are shared. That is
    not a loophole in the tenancy rule but the point of a shared corpus: the second account to be
    shown a job pays nothing for it, and nobody's bandwidth is spent on anybody else's behalf
    without them having caused a fetch.

    A user with no credential is skipped silently. That is the ordinary case, not an error — they
    still get the two thirds of sources that cost nothing.
    """
    corpus = CorpusRepository(session)
    usage = ProxyUsageRepository(session)
    store = SecretStore(session)
    totals: dict[str, int] = {"users": 0, "attempted": 0, "described": 0}

    for user_id in list(session.scalars(select(User.id).where(User.active))):
        # The plaintext lives inside `store.use` and nowhere else — the whole fetch happens within
        # the block, and the client is closed with it, so the proxy URL never outlives the requests
        # it authorised (`TENANT_SAFETY.md` §2).
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
                    usage.record_usage(user_id, megabytes=client.megabytes_downloaded)
                if report.proxy_error:
                    # Recorded against the credential, not swallowed. Three of these quarantine it,
                    # which is what stops an exhausted account being retried every hour forever —
                    # and what makes "why is my LinkedIn feed empty" answerable.
                    usage.record_failure(user_id, report.proxy_error[:200])
                    totals["proxy_failures"] = totals.get("proxy_failures", 0) + 1

        totals["attempted"] += report.attempted
        totals["described"] += report.described

    logger.info("describe(linkedin): %d user(s), %d attempted, %d described",
                totals["users"], totals["attempted"], totals["described"])
    return totals
