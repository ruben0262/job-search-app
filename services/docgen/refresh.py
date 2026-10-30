"""Check the posting is still live before spending anything on it."""
from __future__ import annotations

import logging

from libs.corpus.postings import fetch_meta
from libs.corpus.repository import CorpusRepository

logger = logging.getLogger(__name__)


class PostingClosed(RuntimeError):
    """The posting says it is no longer accepting applications."""


def refresh_before_build(db, job, client) -> dict:
    """Re-read the posting's live metadata, store it, and raise if it has closed.

    A row already marked closed is answered from the column: the fetch costs the user's own metered
    proxy bandwidth, and it cannot tell us anything the corpus does not already know.
    """
    if job.closed_at is not None:
        logger.info("docgen: %s was already closed, refusing without a fetch", job.url)
        raise PostingClosed(
            "this posting is no longer accepting applications — nothing was built or charged")

    meta = fetch_meta(job.url, client=client)

    corpus = CorpusRepository(db)
    corpus.set_posting_metadata(
        job.id, applicants=meta["applicants"], easy_apply=meta["easy_apply"],
        reposted=meta["reposted"], workplace=meta["workplace"], posted_at=meta["posted_at"])
    if meta["application_url"] and not job.application_url:
        corpus.set_application_url(job.id, meta["application_url"])

    if meta["accepting"] is False:
        logger.info("docgen: %s has closed, refusing to build", job.url)
        raise PostingClosed(
            "this posting is no longer accepting applications — nothing was built or charged")

    return meta
