"""The front page and the jobs page. Both ask `libs.matching`, the one answer to "which jobs"."""
from __future__ import annotations

import logging
import uuid as _uuid
from collections import Counter
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from pymongo.database import Database

from libs import limits
from libs.corpus import salary
from libs.corpus.geo import CITIES, COUNTRY_LABELS
from libs.corpus.postings import paragraphs
from libs.db import get, now
from libs.db.models import BuiltDocument, CorpusJob, User, UserJobState
from libs.db.models.tenant import record_state
from libs.documents.saved import for_jobs
from libs.llm import PLATFORM, ai_requests_today, resolve
from libs.matching import Match, matching_jobs, norm
from libs.notify import reachable
from libs.obs import audit
from libs.onboarding import onboarding_state
from libs.profiles.repository import ProfileFiles
from libs.tenant import applications
from services.api.admin_routes import admin_emails
from services.api.deps import current_user, get_db
from services.api.onboarding_routes import OnboardingSteps
from services.api.saved_routes import JobDocument, job_document

logger = logging.getLogger(__name__)
router = APIRouter(tags=["dashboard"])

FEED_PAGE = 25

# How much of a description a job card shows before "Show the full description".
SUMMARY_CHARS = 280


class DashboardBanner(BaseModel):
    level: Literal["error", "warning", "info"]
    key: str
    text: str


class OnboardingDashboard(BaseModel):
    stage: Literal["onboarding"]
    # `onboarding_state(...).as_dict()`, in onboarding's own model: its pages are onboarding's to define.
    onboarding: OnboardingSteps
    is_admin: bool


class AiWriting(BaseModel):
    """Whether an AI can write this person a document now, and on whose key — `libs.llm.resolve`'s
    answer, so the buttons offer exactly what a build would be allowed to do."""
    # "own": their own key, unlimited. "platform": the daily allowance. None: nobody can pay now.
    payer: Literal["own", "platform"] | None
    # The platform's daily allowance, and what is left of it today.
    per_day: int
    left: int
    # The writing profile an AI document is written from. Without it, only instant ones.
    profile: bool


class ReadyDashboard(BaseModel):
    """What every signed-in page needs before it draws. No count of matches: the jobs page is the
    front page and counts its own list, and asking here as well was a second full match run on every
    load — /dashboard went from 150 ms to 13 ms (median, 2,000 rows) without it."""
    stage: Literal["ready"]
    is_admin: bool
    # How many jobs they said they applied to — moved only by "I applied".
    applied: int
    banners: list[DashboardBanner]
    ai: AiWriting


class FeedSalary(BaseModel):
    min: float
    max: float
    currency: str
    period: str
    raw: str


class FeedJob(BaseModel):
    id: int
    title: str
    company: str | None
    location: str | None
    # The country the place was read as, as an ISO code: what the card names first.
    country: str | None
    is_remote: bool | None
    url: str
    source: str
    # The green words that matched, anywhere in the job: why it is on the page.
    words: list[str]
    posted_at: datetime | None
    first_seen_at: datetime | None
    salary: FeedSalary | None
    # The start of the description, whitespace folded, cut at a word: what the card shows. None until
    # the posting has been described.
    summary: str | None
    # Whether there is more of it than the summary, to fetch from `/jobs/{id}/description`.
    more: bool
    # The CVs and cover letters made for it, newest first: still downloadable after a refresh.
    documents: list[JobDocument]


class FeedSource(BaseModel):
    name: str
    # How many of their jobs came from it, before any filter.
    jobs: int


class FeedWord(BaseModel):
    name: str
    # How many of their jobs have it, before any filter.
    jobs: int


class FeedPlace(BaseModel):
    # A country's ISO code, or a city's slug (`libs.corpus.geo.CITIES`).
    value: str
    label: str
    # How many of their jobs are there, before any filter.
    jobs: int


class Feed(BaseModel):
    total: int
    jobs: list[FeedJob]
    # The boards their jobs came from, for the filter that picks one.
    sources: list[FeedSource]
    # Their words found in their jobs, most found first, for the filter that picks some.
    words: list[FeedWord]
    # The countries and the cities their jobs are in, most jobs first, for the filters that pick one.
    countries: list[FeedPlace]
    cities: list[FeedPlace]
    # When this list was read: jobs found after it are the new ones (`found_after`).
    as_of: datetime


def _is_admin(user: User) -> bool:
    """The same rule `require_admin` enforces, in one place."""
    return bool(user.is_admin) or user.email.lower() in admin_emails()


def _marks(db: Database, user_id: _uuid.UUID) -> tuple[set[int], dict[int, datetime]]:
    """The jobs this person skipped, and when they applied to which."""
    rows = list(db[UserJobState.COLLECTION].find(
        {"user_id": user_id, "$or": [{"dismissed_at": {"$ne": None}}, {"applied_at": {"$ne": None}}]},
        {"job_id": 1, "dismissed_at": 1, "applied_at": 1}))
    return ({row["job_id"] for row in rows if row["dismissed_at"]},
            {row["job_id"]: row["applied_at"] for row in rows if row["applied_at"]})


@router.get("/dashboard")
def dashboard(db: Database = Depends(get_db), user_id: _uuid.UUID = Depends(current_user)
              ) -> OnboardingDashboard | ReadyDashboard:
    """Everything the front page shows, in one call."""
    user = get(db, User, user_id)
    state = onboarding_state(db, user_id)
    # Finished once is not enough: a search saved before the minimums (owner, 2026-11-04) is finished on
    # its page, since without a country no job comes in for it.
    unfinished = {page.key for page in state.pages if not page.complete}
    if user.onboarding_completed_at is None or "review" in unfinished:
        # Nothing below is meaningful yet, and empty panels read as a broken product.
        return OnboardingDashboard(stage="onboarding", is_admin=_is_admin(user),
                                   onboarding=OnboardingSteps(**state.as_dict()))

    _, applied = _marks(db, user_id)
    return ReadyDashboard(stage="ready", is_admin=_is_admin(user), applied=len(applied),
                          banners=_banners(db, user), ai=_ai(db, user))


def _ai(db: Database, user: User) -> AiWriting:
    payer = resolve(db, user)
    allowance = limits.resolve(user, "ai_platform_requests_per_day")
    # Never Unlimited (`allow_unlimited=False`): a `None` written by hand is no allowance, as
    # `libs.llm.resolve` reads it too.
    per_day = 0 if allowance is None else allowance
    return AiWriting(payer=None if payer is None else "platform" if payer.kind == PLATFORM else "own",
                     per_day=per_day, left=max(per_day - ai_requests_today(db, user.id), 0),
                     profile=ProfileFiles(db, user.id).build_blocker() is None)


def _banners(db: Database, user: User) -> list[DashboardBanner]:
    """What the jobs page says above the list, one thing at a time. Alerts are optional, so having
    none is a quiet offer rather than a warning — and a Telegram that stopped is a button to press."""
    if user.telegram_status == "broken":
        return [DashboardBanner(level="warning", key="telegram_broken",
                                text="Telegram stopped taking our messages.")]
    if not db[User.COLLECTION].count_documents({"_id": user.id, **reachable(db, user.id)}, limit=1):
        return [DashboardBanner(level="info", key="no_alerts",
                                text="Get alerts on your phone when a new job matches your words.")]
    if not user.active:
        return [DashboardBanner(level="info", key="paused", text="Your alerts are paused.")]
    return []


@router.get("/feed")
def feed(*, db: Database = Depends(get_db), user_id: _uuid.UUID = Depends(current_user),
         limit: int = Query(FEED_PAGE, ge=1, le=100),
         offset: int = Query(0, ge=0),
         q: str | None = Query(None, max_length=200),
         remote: bool | None = None,
         job: int | None = None,
         since_hours: int | None = Query(None, ge=1, le=24 * 60),
         source: str | None = Query(None, max_length=60),
         country: str | None = Query(None, max_length=2),
         city: str | None = Query(None, max_length=40),
         made: bool | None = None,
         word: list[str] = Query([], max_length=50),
         min_words: int = Query(1, ge=1, le=50),
         found_after: datetime | None = None,
         sort: Literal["best", "newest", "arrived"] = "best") -> Feed:
    """This person's matches, a page at a time: the most of their words first, the newest posted
    first, or the ones that arrived here last first:
    searched, remote only, posted lately, from one board, in one country or one city, with a document
    made or not, having every word picked, or at least so many of their words — or one job, the one
    an alert's link names, which is either still in their list or not. `found_after` keeps only the
    jobs found since a list was read, which is how the page learns how many new ones there are
    without reloading.

    A job they skipped or applied to is gone from it for good: Applied lists the second."""
    as_of = now()
    skipped, applied_at = _marks(db, user_id)
    visible = [match for match in matching_jobs(db, get(db, User, user_id), since=found_after)
               if match.job.id not in skipped and match.job.id not in applied_at]
    made_for = (set(db[BuiltDocument.COLLECTION].distinct("job_id", {"user_id": user_id}))
                if made is not None else set())
    cutoff = now() - timedelta(hours=since_hours) if since_hours else None
    needle = (q or "").lower()
    picked = {norm(each) for each in word if each.strip()}
    # `None` is a filter that is off, so `x in (None, value)` reads "off, or equal".
    matches = [match for match in visible
               if needle in f"{match.job.title} {match.job.company or ''} {match.job.url}".lower()
               and remote in (None, bool(match.job.is_remote))
               and job in (None, match.job.id)
               and (cutoff is None or _when(match) >= cutoff)
               and source in (None, match.job.source)
               and country in (None, match.job.country)
               and city in (None, match.job.city)
               and made in (None, match.job.id in made_for)
               and picked <= set(match.words)
               and len(match.words) >= min_words]
    if sort == "newest":
        matches.sort(key=_when, reverse=True)
    elif sort == "arrived":
        # When it came in here, whatever date the board puts on it: a job posted a week ago and found
        # an hour ago is new to them.
        matches.sort(key=lambda match: (match.job.first_seen_at, match.job.id), reverse=True)
    page = matches[offset:offset + limit]
    ids = [match.job.id for match in page]
    # The text of the page's jobs only: matching never brings it back.
    texts = {row["_id"]: row.get("description") for row in db[CorpusJob.COLLECTION].find(
        {"_id": {"$in": ids}}, {"description": 1})}
    documents = for_jobs(db, user_id, ids)
    boards = Counter(match.job.source for match in visible)
    words = Counter(each for match in visible for each in match.words)
    countries = Counter(match.job.country for match in visible if match.job.country)
    cities = Counter(match.job.city for match in visible if match.job.city)
    return Feed(total=len(matches),
                jobs=[feed_row(match, texts.get(match.job.id), documents.get(match.job.id, []))
                      for match in page],
                sources=[FeedSource(name=name, jobs=count) for name, count in boards.most_common()],
                words=[FeedWord(name=name, jobs=count) for name, count in words.most_common()],
                countries=[FeedPlace(value=code, label=COUNTRY_LABELS.get(code, code), jobs=count)
                           for code, count in countries.most_common()],
                cities=[FeedPlace(value=slug, label=CITIES[slug].label if slug in CITIES else slug,
                                  jobs=count) for slug, count in cities.most_common()],
                as_of=as_of)


def _when(match: Match) -> datetime:
    """When a job appeared: its posting date where the board gives one, else when it was found."""
    return match.job.posted_at or match.job.first_seen_at


def feed_row(match: Match, text: str | None, documents: list[dict]) -> FeedJob:
    """One job, with the start of its text and the salary in it, not the whole of it."""
    job = match.job
    return FeedJob(id=job.id, title=job.title, company=job.company, location=job.location,
                   country=job.country, is_remote=job.is_remote, url=job.url, source=job.source,
                   words=list(match.words), posted_at=job.posted_at,
                   first_seen_at=job.first_seen_at, salary=salary.extract(text), **_summary(text),
                   documents=[job_document(row) for row in documents])


def _summary(description: str | None) -> dict:
    """The first `SUMMARY_CHARS` of a description, on one line, cut at a word."""
    text = " ".join((description or "").split())
    if len(text) <= SUMMARY_CHARS:
        return {"summary": text or None, "more": False}
    cut = text[:SUMMARY_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return {"summary": f"{cut}…", "more": True}


class JobDescription(BaseModel):
    text: str


@router.get("/jobs/{job_id}/description")
def job_description(job_id: int, db: Database = Depends(get_db),
                    _user_id: _uuid.UUID = Depends(current_user)) -> JobDescription:
    """A posting's whole description, for a card that was asked to show it: as stored, or in paragraphs
    if it was stored as one line (`paragraphs`)."""
    found = db[CorpusJob.COLLECTION].find_one({"_id": job_id}, {"description": 1})
    if not found or not found.get("description"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no description for that job")
    return JobDescription(text=paragraphs(found["description"]))


class Step(BaseModel):
    """One thing that happened after "I applied", in the person's own words."""
    stage: Literal["interview", "offer", "hired", "rejected"]
    at: datetime


class AppliedJob(BaseModel):
    id: int
    title: str
    company: str | None
    location: str | None
    url: str
    applied_at: datetime
    documents: list[JobDocument]
    # Oldest first; the last is where the application stands, and none means it is waiting.
    progress: list[Step]


@router.get("/applied")
def applied(db: Database = Depends(get_db),
            user_id: _uuid.UUID = Depends(current_user)) -> list[AppliedJob]:
    """Every job this person said they applied to, the latest first, whether or not it still matches
    their words. Retention keeps these postings (`libs.corpus.archive.ACTED_ON`)."""
    rows = list(db[UserJobState.COLLECTION].find(
        {"user_id": user_id, "applied_at": {"$ne": None}}, {"job_id": 1, "applied_at": 1, "progress": 1},
        sort=[("applied_at", -1)]))
    jobs = {doc["_id"]: doc for doc in db[CorpusJob.COLLECTION].find(
        {"_id": {"$in": [row["job_id"] for row in rows]}},
        {"title": 1, "company": 1, "location": 1, "url": 1})}
    documents = for_jobs(db, user_id, list(jobs))
    return [AppliedJob(id=row["job_id"], title=job["title"], company=job.get("company"),
                       location=job.get("location"), url=job["url"], applied_at=row["applied_at"],
                       documents=[job_document(each) for each in documents.get(row["job_id"], [])],
                       progress=row.get("progress") or [])
            for row in rows if (job := jobs.get(row["job_id"]))]


class StepRequest(BaseModel):
    stage: Literal["interview", "offer", "hired", "rejected"]


# Before `/jobs/{job_id}/{mark}`, which would otherwise take "progress" for a mark and refuse it.
@router.put("/jobs/{job_id}/progress")
def advance(job_id: int, body: StepRequest, db: Database = Depends(get_db),
            user_id: _uuid.UUID = Depends(current_user)) -> list[Step]:
    """Say what happened next with an application: an interview, an offer, the job, or not this time."""
    try:
        progress = applications.advance(db, user_id, job_id, body.stage)
    except applications.StepRefused as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    audit(logger, "application.progressed", user=user_id, job=job_id, stage=body.stage)
    return progress


@router.delete("/jobs/{job_id}/progress")
def step_back(job_id: int, db: Database = Depends(get_db),
              user_id: _uuid.UUID = Depends(current_user)) -> list[Step]:
    """Take the last step back, for a tap made by mistake."""
    audit(logger, "application.stepped_back", user=user_id, job=job_id)
    return applications.step_back(db, user_id, job_id)


class JobMarks(BaseModel):
    job_id: int
    applied: bool
    hidden: bool


# "I applied" and "Skip" — the only two things a person records about a job, and nothing else does:
# making a document is not applying. Either takes the job off their jobs page for good. Skip is
# `hidden` here, as it was called before the page renamed it.
_MARKS = {"applied": "applied_at", "hidden": "dismissed_at"}


@router.put("/jobs/{job_id}/{mark}", response_model=JobMarks)
@router.delete("/jobs/{job_id}/{mark}", response_model=JobMarks)
def mark_job(job_id: int, mark: Literal["applied", "hidden"], request: Request,
             db: Database = Depends(get_db),
             user_id: _uuid.UUID = Depends(current_user)) -> JobMarks:
    """PUT records the mark, DELETE takes it back. Only ever this person's own row."""
    if not db[CorpusJob.COLLECTION].count_documents({"_id": job_id}, limit=1):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such job")
    state = record_state(db, user_id, job_id, **{_MARKS[mark]: now() if request.method == "PUT" else None})
    audit(logger, "job.marked", user=user_id, job=job_id, mark=mark, on=request.method == "PUT")
    return JobMarks(job_id=job_id, applied=state.applied_at is not None,
                    hidden=state.dismissed_at is not None)
