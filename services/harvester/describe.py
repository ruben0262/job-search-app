"""Fill in the descriptions the listing APIs did not give us."""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from time import monotonic

from libs.corpus.postings import Posting, ProxyUnusable, fetch_job
from libs.corpus.repository import DESCRIBE_MAX_FAILURES, CorpusRepository
from libs.db.models import CorpusJob
from libs.obs import audit

logger = logging.getLogger(__name__)

# How many rows one pass will fetch.
SHARED_LIMIT = 200


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
    # Postings the board would not answer for, every try. Asked again next pass, never abandoned.
    throttled: int = 0

    @property
    def hit_rate(self) -> float:
        return self.described / self.attempted if self.attempted else 0.0


def describe_pending(corpus: CorpusRepository, client, *, linkedin: bool | None = None,
                     limit: int | None = SHARED_LIMIT,
                     budget_seconds: float | None = None,
                     max_age_hours: int | None = None,
                     workers: int = 1) -> DescribeReport:
    """Fetch and store descriptions for corpus rows that are still thin, `workers` pages at a time.

    `limit` of `None` is every row waiting; `budget_seconds` is then what bounds the pass.
    `max_age_hours` is the operator's delivery window. A posting past it cannot reach anybody, so
    fetching its text is bandwidth spent on a row the pre-score filter has already refused.

    Pages are fetched in parallel and written one at a time, here: the fetch is the slow part (a
    second or more each through the site proxy, measured 2026-11-02), and the writes stay in order.
    """
    report = DescribeReport()
    jobs = corpus.awaiting_description(limit=limit or 0, linkedin=linkedin,
                                       max_age_hours=max_age_hours)
    deadline = None if budget_seconds is None else monotonic() + budget_seconds
    refused = threading.Event()
    # As many pages in a row as there are readers, each refused on every try and every address: LinkedIn
    # is out of allowance for now, and asking on would spend what the next pass needs (measured
    # 2026-11-02: past it, 880 of 991 tries were refused; 25 minutes later, 10 of 12 were read).
    busy, lock, in_a_row = threading.Event(), threading.Lock(), [0]

    def fetch(job: CorpusJob) -> tuple[Posting | ProxyUnusable, dict[str, int]] | None:
        """The page and how it went, or `None` when the pass is out of time, the proxy refused, or the
        board is refusing everything."""
        if refused.is_set() or busy.is_set() or (deadline is not None and monotonic() >= deadline):
            return None
        outcomes: dict[str, int] = {}
        try:
            posting = fetch_job(job.url, client=client, snippet=job.description or "",
                                title=job.title or "", stats=outcomes)
        except ProxyUnusable as exc:
            # The credential is the problem, not this job, and no other job will fare better.
            refused.set()
            return exc, outcomes
        with lock:
            in_a_row[0] = in_a_row[0] + 1 if posting.get("throttled") else 0
            if in_a_row[0] >= max(workers, 1):
                busy.set()
        return posting, outcomes

    skipped = 0
    with ThreadPoolExecutor(max_workers=max(workers, 1)) as pool:
        for job, fetched in zip(jobs, pool.map(fetch, jobs), strict=True):
            if fetched is None:
                skipped += 1
                continue
            posting, outcomes = fetched
            for key, count in outcomes.items():
                report.outcomes[key] = report.outcomes.get(key, 0) + count
            if isinstance(posting, ProxyUnusable):
                report.proxy_error = str(posting)
                logger.warning("describe: stopping, the proxy refused: %s", posting)
                continue
            report.attempted += 1
            _store(corpus, job, posting, report)

    if skipped and busy.is_set():
        logger.warning("describe: stopping at %d of %d, every try refused for %d page(s) in a row — "
                       "the rest are first in line next run", report.attempted, len(jobs), in_a_row[0])
    elif skipped and not report.proxy_error:
        logger.info("describe: stopping at %d of %d, the pass is out of time — the rest are "
                    "first in line next run", report.attempted, len(jobs))
    logger.info("describe: %d attempted, %d described, %d still thin, %d throttled — %s",
                report.attempted, report.described, report.still_thin, report.throttled,
                report.outcomes)
    audit(logger, "harvest.describe", attempted=report.attempted, described=report.described,
          still_thin=report.still_thin, abandoned=report.abandoned, throttled=report.throttled,
          linkedin=linkedin, outcomes=report.outcomes)
    return report


def _store(corpus: CorpusRepository, job: CorpusJob, posting: Posting, report: DescribeReport) -> None:
    """What one page said, written to the shared corpus."""
    if posting.get("accepting") is False:
        # The one fetch we were always going to make is also the one that can tell us the
        # posting is dead — so this costs nothing and stops every later stage paying for it.
        corpus.mark_closed(job.id)
        report.closed += 1
        return

    if posting.get("throttled"):
        # Every try was refused or failed in transit: that says nothing about the posting, so it
        # is not counted against it. Counting it abandoned live jobs whenever LinkedIn was busy.
        report.throttled += 1
        return

    # Metadata is worth keeping even when the description came back thin: the card and filter read it.
    if corpus.set_posting_metadata(
            job.id, applicants=posting["applicants"],
            applicants_caption=posting.get("applicants_caption"),
            reposted=posting["reposted"],
            workplace=posting["workplace"], posted_at=posting["posted_at"]):
        report.metadata_written += 1

    if posting["application_url"] and not job.application_url:
        # Both the link the user needs at apply time and the strongest cross-source dedup key there is.
        corpus.set_application_url(job.id, posting["application_url"])

    text = posting.description
    if len(text) > len(job.description or ""):
        corpus.set_description(job.id, text)
        corpus.clear_describe_failures(job.id)
        report.described += 1
        return
    # The page gave us nothing better than we already had. Counted on the row, because a
    # describer with no memory asks the same dead posting again every pass, forever.
    failures = corpus.record_describe_failure(job.id)
    report.still_thin += 1
    if failures >= DESCRIBE_MAX_FAILURES:
        report.abandoned += 1
        logger.warning("describe: giving up on %s after %d failed reads — it will not be "
                       "described again", job.url, failures)
