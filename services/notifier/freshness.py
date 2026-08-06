"""Date the candidates before deciding which are too old to send."""
from __future__ import annotations

import logging
import time
from dataclasses import replace

from libs.corpus.postings import ProxyUnusable, fetch_meta, linkedin_id
from libs.corpus.repository import CorpusRepository

logger = logging.getLogger(__name__)

# About one digest's worth.
MAX_REFRESH = 12

# A wall-clock budget, and it is the limit that actually binds.
REFRESH_BUDGET_SECONDS = 30.0


def date_candidates(session, items: list, client) -> list:
    """Ask each posting near the front of the queue what it says about itself now."""
    corpus = CorpusRepository(session)
    updated: list = []
    dated = 0
    deadline = time.monotonic() + REFRESH_BUDGET_SECONDS

    for item in items:
        # The gate used to be `item.posted_at is not None`, and it stopped this running at all.
        if dated >= MAX_REFRESH or time.monotonic() >= deadline or not linkedin_id(item.url):
            updated.append(item)
            continue

        try:
            meta = fetch_meta(item.url, client=client)
        except ProxyUnusable as exc:
            # The credential, not this job.
            logger.warning("freshness: stopping, the proxy refused: %s", exc)
            updated.append(item)
            updated.extend(items[len(updated):])
            break

        if meta["accepting"] is False:
            corpus.mark_closed(item.job_id)
            updated.append(replace(item, closed=True))
            continue

        # A `None` never overwrites a known value, so a partial fetch still contributes what it had.
        corpus.set_posting_metadata(
            item.job_id, applicants=meta["applicants"],
            applicants_caption=meta["applicants_caption"], reposted=meta["reposted"],
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
