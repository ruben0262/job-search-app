"""A job the user pasted, turned into a corpus row.

`/cv <link>` and `/cover <link>` are the reference's answer to a job found somewhere the harvest
never looked — a friend's message, a company's own careers page, a post on a forum. The user should
not have to explain why the platform has not seen it; they paste the link and get the document.

**It goes into the shared corpus rather than into a side table.** Three things fall out of that,
and the third is the one that matters:

- the build path is the ordinary one, so a pasted job produces exactly the same document as a
  delivered one rather than travelling through a second code path nobody exercises;
- the applications page and the training data see it, because it is a job the user acted on;
- **the next user to be shown that job gets it already described.** A link pasted once is a link
  fetched once, for everybody.

Dedup runs first and runs the same four layers everything else does. A pasted LinkedIn link very
often resolves to a Greenhouse posting already in the corpus, and adding a second row would give
the user two different feed entries for one job and split its history in half.
"""
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
    # Whether this was already in the corpus. Worth reporting back: "you have seen this one" is a
    # more useful answer than silently building a second copy.
    existing: bool


def resolve(session, url: str, client) -> Resolved:
    """Find or create the corpus row for a pasted job URL.

    Raises `NotAJobLink` rather than returning a half-row: a build against a job with no title and
    no description would produce a document about nothing, and it would cost the user to find out.
    """
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

    # Dedup again with what the page told us: a title and a company make the content key usable,
    # and that is the layer that catches the same role listed on two boards.
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
