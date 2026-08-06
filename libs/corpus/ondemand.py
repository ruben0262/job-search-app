"""A job the user pasted, turned into a corpus row."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlparse

from libs.corpus.postings import fetch_posting
from libs.corpus.repository import CorpusRepository
from libs.db.models import CorpusJob

logger = logging.getLogger(__name__)


class NotAJobLink(ValueError):
    """The text was not a usable URL, or the page yielded nothing to work with."""


@dataclass(frozen=True)
class Resolved:
    """What the paste turned into."""

    job_id: int
    title: str
    company: str
    # Whether this was already in the corpus.
    existing: bool


def resolve(session, url: str, client) -> Resolved:
    """Find or create the corpus row for a pasted job URL."""
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise NotAJobLink("that does not look like a link — paste the job's URL")

    corpus = CorpusRepository(session)
    duplicate = corpus.find_duplicate(url, "", "", "")
    if duplicate is not None:
        job = session.get(CorpusJob, duplicate.job_id)
        return Resolved(duplicate.job_id, job.title or "", job.company or "", existing=True)

    posting = fetch_posting(url, client)
    title = (posting.get("title") or "").strip()
    description = (posting.get("description") or "").strip()
    if not title and len(description) < 100:
        raise NotAJobLink(
            "could not read that page — some job boards block us. Try the company's own listing")

    # Dedup again with the page's title and company: the layer that catches one role on two boards.
    duplicate = corpus.find_duplicate(url, title, posting.get("company") or "",
                                      posting.get("location") or "")
    if duplicate is not None:
        job = session.get(CorpusJob, duplicate.job_id)
        return Resolved(duplicate.job_id, job.title or title, job.company or "", existing=True)

    job_id = corpus.add(url=url, source=posting.get("source") or "manual",
                        title=title or url, company=posting.get("company") or "",
                        location=posting.get("location") or "")
    if job_id is None:
        # Inserted by a concurrent path between the check and the insert. Not an error.
        duplicate = corpus.find_duplicate(url, title, "", "")
        if duplicate is None:
            raise NotAJobLink("could not store that job — try again in a moment")
        job_id = duplicate.job_id
    elif description:
        corpus.set_description(job_id, description)

    logger.info("ondemand: added %s from a pasted link", url)
    return Resolved(job_id, title, posting.get("company") or "", existing=False)
