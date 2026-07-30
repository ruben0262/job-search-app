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

from libs.corpus.postings import ProxyUnusable, fetch_meta, linkedin_id
from libs.corpus.repository import CorpusRepository

logger = logging.getLogger(__name__)

# About one digest's worth. The candidates are five times that, and dating a job nobody was going
# to be sent spends the user's bandwidth on nothing.
MAX_REFRESH = 12


def date_candidates(session, items, client) -> int:
    """Fill `posted_at` for the undated jobs nearest the front of the queue. Returns how many.

    `items` is the candidate list in score order, so taking from the front dates the ones most
    likely to be sent. LinkedIn only: nothing else exposes a posted time on the page that the
    listing API did not already give us, so a request would be spent to learn nothing.

    Mutates the `FeedItem`s in place as well as the corpus row, because the caller is about to
    filter on exactly these objects and re-reading them from the database would be a second query
    for data already in hand.
    """
    corpus = CorpusRepository(session)
    dated = 0

    for item in items:
        if dated >= MAX_REFRESH:
            break
        if item.posted_at is not None or not linkedin_id(item.url):
            continue

        try:
            meta = fetch_meta(item.url, client=client)
        except ProxyUnusable as exc:
            # The credential, not this job. Every remaining fetch would fail the same way.
            logger.warning("freshness: stopping, the proxy refused: %s", exc)
            break

        if meta["accepting"] is False:
            corpus.mark_closed(item.job_id)
            item.closed = True

        if meta["posted_at"] is None:
            continue

        corpus.set_posting_metadata(
            item.job_id, applicants=meta["applicants"], reposted=meta["reposted"],
            workplace=meta["workplace"], posted_at=meta["posted_at"])
        item.posted_at = meta["posted_at"]
        item.applicants = meta["applicants"] if meta["applicants"] is not None else item.applicants
        item.reposted = meta["reposted"] if meta["reposted"] is not None else item.reposted
        dated += 1

    if dated:
        logger.info("freshness: dated %d candidate(s) before selection", dated)
    return dated
