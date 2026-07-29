"""Check the posting is still live before spending anything on it.

**The reference refreshes before it builds, and the platform did not.** A CV costs the user real
money and about a minute of model time; a cover letter costs the same again. Spending that on a
listing that closed three days ago is the most annoying possible failure, because everything
succeeds — the document arrives, it is good, and the apply link leads to "no longer accepting
applications".

It is one cheap request: `fetch_meta` reads the guest fragment and no description, so nothing here
re-pays for text already stored.

**Only a positive "closed" stops a build.** A fetch that fails, a non-LinkedIn URL, a page that
does not say either way — all proceed. The asymmetry is the same one every filter in this codebase
uses, and it is starker here: refusing to build on a failed fetch would mean a proxy hiccup
presenting to the user as "that job is gone".

The refreshed metadata is written back to the shared corpus on the way through. The user paid for
the request; everyone gets the applicant count.
"""
from __future__ import annotations

import logging

from libs.corpus.postings import fetch_meta
from libs.corpus.repository import CorpusRepository

logger = logging.getLogger(__name__)


class PostingClosed(RuntimeError):
    """The posting says it is no longer accepting applications. Terminal — there is nothing to
    retry, and the money would be spent on a document nobody can send."""


def refresh_before_build(session, job, client) -> dict:
    """Re-read the posting's live metadata, store it, and raise if it has closed.

    Returns what was read, so a caller can put a current applicant count on the document's caption
    rather than one from whenever the job was harvested.
    """
    meta = fetch_meta(job.url, client=client)

    corpus = CorpusRepository(session)
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
