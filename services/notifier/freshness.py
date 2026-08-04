"""Date the candidates before deciding which are too old to send.

**The age filter can only drop what it can date, and most of the feed had no date.** Of 216 jobs
pending delivery on 2026-07-30, 68 carried a `posted_at`. The rest fell through the filter
open — which is the correct default for an unknown, and is how "Reposted 1 month ago" kept arriving
as a live card.

Fixing the harvest window stops *new* stale rows entering. It does nothing about the ones already
in the corpus, and nothing about LinkedIn, which reports no date to the listing API at all: its
posted time lives on the posting's own page and nowhere else.

So the dates are fetched here, for the handful of jobs actually about to be sent. That is the
reference's order too — it refreshes a posting immediately before surfacing it, because a listing
is at its most interesting and its most likely to have gone stale at exactly that moment.

## Why this is cheap enough to do every time

Bounded by `MAX_REFRESH`, which is roughly one digest. Not the candidate pool — that is five times
the digest, and dating jobs that were never going to be sent is bandwidth spent on nothing.
`fetch_meta` reads the guest fragment and no description, so each one is a small request on the
user's own proxy, and what it learns is written to the shared corpus where the next user gets it
free.

## Failing open is still the rule

A fetch that does not answer leaves the row undated and the job is sent. A proxy outage must not
empty somebody's feed — a stale card is an annoyance and an empty feed reads as a broken product.
What changes is that "we never asked" is no longer indistinguishable from "we asked and it is
fresh".
"""
from __future__ import annotations

import logging
import time
from dataclasses import replace

from libs.corpus.postings import ProxyUnusable, fetch_meta, linkedin_id
from libs.corpus.repository import CorpusRepository

logger = logging.getLogger(__name__)

# About one digest's worth. The candidates are five times that, and refreshing a job nobody was
# going to be sent spends the user's bandwidth on nothing.
MAX_REFRESH = 12

# **A wall-clock budget, and it is the limit that actually binds.** Each refresh is a proxied fetch
# with retries and backoff, so twelve of them can take minutes — and the `notify` lane's claim
# timeout is two. A job that overruns it is reclaimed mid-flight, re-runs, overruns again, and
# `notified_at` is never committed: the account simply stops receiving anything, with every job
# reported done. That is what happened the first hour this refresh was allowed to run at all.
#
# Thirty seconds leaves the rest of the two minutes for rendering and sending. Whatever is not
# refreshed by then is passed through unchanged, which is the same fail-open rule a proxy error
# already follows — a stale card is an annoyance, an empty feed reads as a broken product.
REFRESH_BUDGET_SECONDS = 30.0


def date_candidates(session, items: list, client) -> list:
    """Ask each posting near the front of the queue what it says about itself now.

    **Four answers, and the date is the least of them**: whether it is still accepting applications,
    how many people have applied, whether it is a repost, and when it was posted. Returns the new
    list.

    `items` is the candidate list in score order, so taking from the front dates the ones most
    likely to be sent. LinkedIn only: nothing else exposes a posted time on the page that the
    listing API did not already give us, so a request would be spent to learn nothing.

    **Returns replacements rather than mutating.** `FeedItem` is frozen, and deliberately so — it
    is what gets rendered into a message, and a card that can be edited after the decision to send
    it is a card that can disagree with the decision. Each dated job becomes a new item carrying
    what the posting's own page said; the corpus row is updated in the same breath, so the next
    user gets it without a fetch.
    """
    corpus = CorpusRepository(session)
    updated: list = []
    dated = 0
    deadline = time.monotonic() + REFRESH_BUDGET_SECONDS

    for item in items:
        # **The gate used to be `item.posted_at is not None`, and it stopped this running at all.**
        # `posted_at` is now filled for 2,582 of 2,587 LinkedIn rows at ingest, so the refresh
        # skipped everything — and with it went the closed-job check, the live applicant count and
        # the repost flag, which are the three things it is most worth asking about in the seconds
        # before a card is sent. A date was only ever one of the answers.
        if dated >= MAX_REFRESH or time.monotonic() >= deadline or not linkedin_id(item.url):
            updated.append(item)
            continue

        try:
            # The 96 KB Easy Apply page is fetched only for a posting we have never asked about.
            # It does not change, and paying for it every digest is ~110 MB a day against a 200 MB
            # cap for one boolean.
            meta = fetch_meta(item.url, client=client,
                              with_easy_apply=item.easy_apply is None)
        except ProxyUnusable as exc:
            # The credential, not this job. Every remaining fetch would fail the same way, so the
            # rest of the list is passed through undated rather than retried twelve times.
            logger.warning("freshness: stopping, the proxy refused: %s", exc)
            updated.append(item)
            updated.extend(items[len(updated):])
            break

        if meta["accepting"] is False:
            corpus.mark_closed(item.job_id)
            updated.append(replace(item, closed=True))
            continue

        # A `None` never overwrites a known value, so a fetch that answered only some of the
        # questions still contributes what it had.
        corpus.set_posting_metadata(
            item.job_id, applicants=meta["applicants"], reposted=meta["reposted"],
            easy_apply=meta["easy_apply"], workplace=meta["workplace"],
            posted_at=meta["posted_at"])
        updated.append(replace(
            item,
            posted_at=meta["posted_at"] or item.posted_at,
            applicants=meta["applicants"] if meta["applicants"] is not None else item.applicants,
            reposted=meta["reposted"] if meta["reposted"] is not None else item.reposted,
            easy_apply=meta["easy_apply"] if meta["easy_apply"] is not None else item.easy_apply))
        dated += 1

    if dated:
        logger.info("freshness: refreshed %d candidate(s) in %.1fs before selection", dated,
                    REFRESH_BUDGET_SECONDS - max(deadline - time.monotonic(), 0.0))
    return updated
