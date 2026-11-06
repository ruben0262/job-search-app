"""Add a job: somebody pastes a job's link, from anywhere. It is either here already, or it is read from
its page and kept in the shared corpus, for them and for everyone whose words it has. Where the page
will not say all of it, they write in the rest, and a job written in is kept for them alone: nobody
checked it, so it may not reach anybody else (owner, 2026-11-03). Each job they bring is listed for
them on that page until they skip it or apply.

Reading the page is code, never AI (CLAUDE.md §0): JSON-LD, LinkedIn's job card, No Fluff Jobs' API
(`libs.corpus.postings.read_link`). A guess is shown to the person, never kept as it is.
"""
from __future__ import annotations

import ipaddress
import logging
import re
import socket
import uuid as _uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from pymongo.database import Database

from libs.corpus.postings import PAGE_HEADERS, job_link, linkedin_id, read_link
from libs.corpus.repository import LINK, MAX_DESCRIPTION, MIN_DESCRIPTION, TYPED, CorpusRepository
from libs.db import now
from libs.db.models import CorpusJob, UserJobState
from libs.db.models.tenant import record_state
from libs.documents.saved import for_jobs
from libs.matching import Match
from libs.obs import audit
from libs.proxy import site_proxy
from libs.secrets import redacting
from services.api.dashboard_routes import FeedJob, feed_row
from services.api.deps import current_user, get_db, rate_limit

logger = logging.getLogger(__name__)
router = APIRouter(tags=["links"])

# Links one person may have read, or jobs write in, in an hour. Each read is a request from the
# server, a LinkedIn one through the site's proxy, and each job kept is shown to everyone whose words
# it has: a ceiling on a script, as uploads and the contact form have, not on a person.
LINKS_PER_HOUR = 30
# The jobs they brought that the page lists, newest first.
ADDED_SHOWN = 100
# A person waits on the read, and a job's page is a few hundred KB; past this it is not one.
READ_TIMEOUT = 10
MAX_PAGE_BYTES = 5 * 1024 * 1024


class NotPublic(Exception):
    """The link leads into a private network: this server's own, or the one it runs in."""


def _public_only(request: httpx.Request) -> None:
    """Before every request, redirects too: a pasted link reaches the internet and nothing else, or a
    person could have this server read its own network to them as a job's text."""
    try:
        found = socket.getaddrinfo(request.url.host, request.url.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise httpx.ConnectError(f"no such host: {exc}", request=request) from exc
    if not all(ipaddress.ip_address(str(each[4][0]).split("%")[0]).is_global for each in found):
        raise NotPublic(request.url.host)


def _small_enough(response: httpx.Response) -> None:
    size = int(response.headers.get("content-length") or 0)
    if size > MAX_PAGE_BYTES:
        raise httpx.HTTPError(f"{size} bytes is not a job's page")


@contextmanager
def _client(url: str) -> Iterator[httpx.Client]:
    """Direct, or through the site's proxy for LinkedIn, which refuses a server's own address
    (`libs.proxy`). No connection is kept, so each try leaves from the proxy's next address."""
    proxy = site_proxy() if linkedin_id(url) else None
    with redacting(proxy or ""), httpx.Client(
            headers=PAGE_HEADERS, timeout=READ_TIMEOUT, follow_redirects=True, trust_env=False,
            proxy=proxy, limits=httpx.Limits(max_keepalive_connections=0),
            event_hooks={"request": [_public_only], "response": [_small_enough]}) as client:
        yield client


class LinkRequest(BaseModel):
    url: str = Field(min_length=4, max_length=2000)


class TypedJob(BaseModel):
    """A job as the person wrote it in, where its page would not say all of it."""
    url: str = Field(min_length=4, max_length=2000)
    title: str = Field(min_length=1, max_length=200)
    company: str = Field(min_length=1, max_length=200)
    location: str = Field("", max_length=200)
    # Kept to `MAX_DESCRIPTION`, as every description is; a whole page pasted in is not refused for it.
    description: str = Field(max_length=4 * MAX_DESCRIPTION)


class Draft(BaseModel):
    """What the page said, for the person to finish: every field, empty where it said nothing."""
    url: str
    title: str
    company: str
    location: str
    description: str


class LinkAnswer(BaseModel):
    # "known": it was here already. "added": read from its page and kept now. "closed": its page says
    # it has ended. "draft": its page said part of it, or nothing, for the person to finish.
    outcome: Literal["known", "added", "closed", "draft"]
    job: FeedJob | None = None
    draft: Draft | None = None


@router.post("/jobs/link")
def find_by_link(body: LinkRequest, request: Request, db: Database = Depends(get_db),
                 user_id: _uuid.UUID = Depends(current_user)) -> LinkAnswer:
    """A pasted link: the job, if it is here; else read from its page and kept, if the page said all of
    it; else what the page did say, for the person to finish and keep (`POST /jobs/link/save`)."""
    url = _address(body.url)
    corpus = CorpusRepository(db)
    if (known := corpus.find_duplicate(url)) is not None:
        return _answer(db, user_id, "known", known.job_id, url)
    if (mine := _typed_by(db, user_id, url)) is not None:
        return _answer(db, user_id, "known", mine, url)

    rate_limit(request, f"link:{user_id}", limit=LINKS_PER_HOUR, per_seconds=3600)
    found: dict[str, Any] = {}
    try:
        with _client(url) as client:
            found = read_link(url, client)
    except NotPublic as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="That link is not to a public web page.") from exc
    except Exception:
        # Whatever the page did to the reader, the person can still write the job in.
        logger.warning("link: reading %s failed, the person fills it in", urlparse(url).hostname,
                       exc_info=True)
    draft = Draft(url=url, title=found.get("title") or "", company=found.get("company") or "",
                  location=found.get("location") or "",
                  # A guess from a whole page can be longer than a job is kept.
                  description=(found.get("description") or "")[:MAX_DESCRIPTION])
    if found.get("closed"):
        audit(logger, "job.link", user=user_id, outcome="closed", host=urlparse(url).hostname)
        return LinkAnswer(outcome="closed", draft=draft)
    # The same job under another address: a LinkedIn job we have from its company's own board.
    if (known := corpus.find_duplicate(url, draft.title, draft.company, draft.location)) is not None:
        return _answer(db, user_id, "known", known.job_id, url)
    if not found.get("complete"):
        audit(logger, "job.link", user=user_id, outcome="draft", host=urlparse(url).hostname,
              said=[name for name in ("title", "company", "description") if getattr(draft, name)])
        return LinkAnswer(outcome="draft", draft=draft)
    return _answer(db, user_id, "added", _keep(corpus, draft, found), url)


@router.post("/jobs/link/save")
def save_typed(body: TypedJob, request: Request, db: Database = Depends(get_db),
               user_id: _uuid.UUID = Depends(current_user)) -> LinkAnswer:
    """A job the person wrote in, kept for them alone (`CorpusRepository.add_typed`); or the public one,
    if it is here already under this address or this title, company and place."""
    url = _address(body.url)
    if len(body.description.strip()) < MIN_DESCRIPTION:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Paste the whole description from the job's page. A CV is made from it, "
                                   f"so it needs at least {MIN_DESCRIPTION} characters.")
    rate_limit(request, f"link:{user_id}", limit=LINKS_PER_HOUR, per_seconds=3600)
    corpus = CorpusRepository(db)
    typed = Draft(url=url, title=body.title.strip(), company=body.company.strip(),
                  location=body.location.strip(), description=body.description.strip())
    if (known := corpus.find_duplicate(url, typed.title, typed.company, typed.location)) is not None:
        return _answer(db, user_id, "known", known.job_id, url)
    if (mine := _typed_by(db, user_id, url)) is not None:
        return _answer(db, user_id, "known", mine, url)
    job_id = corpus.add_typed(url=url, title=typed.title, company=typed.company, location=typed.location,
                              description=typed.description)
    return _answer(db, user_id, "added", job_id, url, typed=True)


@router.get("/jobs/added")
def added(db: Database = Depends(get_db), user_id: _uuid.UUID = Depends(current_user)) -> list[FeedJob]:
    """The jobs this person brought here by their links, the latest first, until they skip one, apply
    to it, or it closes."""
    rows = db[UserJobState.COLLECTION].find(
        {"user_id": user_id, "added_at": {"$ne": None}, "applied_at": None, "dismissed_at": None},
        {"job_id": 1}, sort=[("added_at", -1)], limit=ADDED_SHOWN)
    return _jobs(db, user_id, [row["job_id"] for row in rows], open_only=True)


def _address(pasted: str) -> str:
    url = job_link(pasted)
    if url is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="That is not a link. Copy the address from the job's page and paste it.")
    return url


def _typed_by(db: Database, user_id: _uuid.UUID, url: str) -> int | None:
    """The job this person wrote in for this page before, which no search finds but theirs."""
    mine = db[UserJobState.COLLECTION].distinct("job_id", {"user_id": user_id, "added_at": {"$ne": None}})
    found = db[CorpusJob.COLLECTION].find_one(
        {"_id": {"$in": mine}, "source": TYPED,
         "url": {"$regex": f"^{re.escape(url.split('#', 1)[0])}#typed-"}}, {"_id": 1})
    return found["_id"] if found else None


def _keep(corpus: CorpusRepository, job: Draft, found: dict[str, Any]) -> int:
    """A job read whole from its page, into the shared corpus as the harvest keeps one: matched, alerted
    and archived like any other. A LinkedIn job is LinkedIn's; anything else is a link's."""
    workplace = found.get("workplace")
    job_id = corpus.add(url=job.url, source="linkedin" if linkedin_id(job.url) else LINK,
                        title=job.title, company=job.company, location=job.location,
                        is_remote=True if workplace == "remote" else None, posted_at=found.get("posted_at"),
                        workplace=workplace, fetched_at=now(), discovered_via="link")
    if job_id is None:
        # The same link, kept by somebody else a moment ago.
        return corpus.find_duplicate(job.url).job_id
    # Its language is read here (`libs.corpus.langfilter`), which is why this image carries langdetect.
    corpus.set_description(job_id, job.description)
    return job_id


def _answer(db: Database, user_id: _uuid.UUID, outcome: str, job_id: int, url: str, *,
            typed: bool = False) -> LinkAnswer:
    """The job as the jobs page shows it, and listed for them among the jobs they added. A job they
    had skipped is theirs again: bringing its link back says they want it."""
    record_state(db, user_id, job_id, added_at=now(), dismissed_at=None)
    audit(logger, "job.link", user=user_id, outcome=outcome, job=job_id, host=urlparse(url).hostname,
          typed=typed)
    return LinkAnswer(outcome=outcome, job=_jobs(db, user_id, [job_id])[0])


def _jobs(db: Database, user_id: _uuid.UUID, ids: list[int], *, open_only: bool = False) -> list[FeedJob]:
    """Cards for these jobs, in this order, with the documents made for each."""
    where: dict[str, Any] = {"_id": {"$in": ids}, **({"closed_at": None} if open_only else {})}
    jobs = {row["_id"]: CorpusJob.from_doc(row) for row in db[CorpusJob.COLLECTION].find(where)}
    documents = for_jobs(db, user_id, list(jobs))
    return [feed_row(Match(job=job, words=(), in_title=()), job.description, documents.get(job_id, []))
            for job_id in ids if (job := jobs.get(job_id))]
