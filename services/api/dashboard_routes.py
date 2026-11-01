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
from libs.db import get, now
from libs.db.models import BuiltDocument, CorpusJob, User, UserJobState
from libs.db.models.tenant import record_state
from libs.documents.saved import for_jobs
from libs.llm import PLATFORM, ai_requests_today, resolve
from libs.matching import Match, matching_jobs
from libs.notify import reachable
from libs.obs import audit
from libs.onboarding import onboarding_state
from libs.profiles.repository import ProfileFiles
from services.api.admin_routes import admin_emails
from services.api.deps import current_user, get_db
from services.api.onboarding_routes import OnboardingSteps
from services.api.saved_routes import JobDocument, job_document

logger = logging.getLogger(__name__)
router = APIRouter(tags=["dashboard"])

FEED_PAGE = 25


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
    is_remote: bool | None
    url: str
    source: str
    # 75 for a green word in the title, 25 for one in the text, 100 for both.
    score: int
    # The green words that matched: why this job is on the page.
    words: list[str]
    posted_at: datetime | None
    first_seen_at: datetime | None
    salary: FeedSalary | None
    applied_at: datetime | None
    # The CVs and cover letters made for it, newest first: still downloadable after a refresh.
    documents: list[JobDocument]


class FeedSource(BaseModel):
    name: str
    # How many of their jobs came from it, before any filter.
    jobs: int


class Feed(BaseModel):
    total: int
    jobs: list[FeedJob]
    # The boards their jobs came from, for the filter that picks one.
    sources: list[FeedSource]


def _is_admin(user: User) -> bool:
    """The same rule `require_admin` enforces, in one place."""
    return bool(user.is_admin) or user.email.lower() in admin_emails()


def _marks(db: Database, user_id: _uuid.UUID) -> tuple[set[int], dict[int, datetime]]:
    """The jobs this person dismissed, and when they applied to which."""
    rows = list(db[UserJobState.COLLECTION].find(
        {"user_id": user_id, "$or": [{"dismissed_at": {"$ne": None}}, {"applied_at": {"$ne": None}}]},
        {"job_id": 1, "dismissed_at": 1, "applied_at": 1}))
    return ({row["job_id"] for row in rows if row["dismissed_at"]},
            {row["job_id"]: row["applied_at"] for row in rows if row["applied_at"]})


def _visible(db: Database, user: User, dismissed: set[int]) -> list[Match]:
    """This person's matches, minus the ones they dismissed."""
    return [match for match in matching_jobs(db, user) if match.job.id not in dismissed]


@router.get("/dashboard")
def dashboard(db: Database = Depends(get_db), user_id: _uuid.UUID = Depends(current_user)
              ) -> OnboardingDashboard | ReadyDashboard:
    """Everything the front page shows, in one call."""
    user = get(db, User, user_id)
    if user.onboarding_completed_at is None:
        # Nothing below is meaningful yet, and empty panels read as a broken product.
        return OnboardingDashboard(stage="onboarding", is_admin=_is_admin(user),
                                   onboarding=OnboardingSteps(**onboarding_state(db, user_id).as_dict()))

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
         applied: bool | None = None,
         made: bool | None = None,
         sort: Literal["best", "newest"] = "best") -> Feed:
    """This person's matches, a page at a time, best first or newest first: searched, remote only,
    posted lately, from one board, applied to or not, with a document made or not — or one job, the
    one an alert's link names, which is either still in their list or not."""
    dismissed, applied_at = _marks(db, user_id)
    visible = _visible(db, get(db, User, user_id), dismissed)
    made_for = (set(db[BuiltDocument.COLLECTION].distinct("job_id", {"user_id": user_id}))
                if made is not None else set())
    cutoff = now() - timedelta(hours=since_hours) if since_hours else None
    needle = (q or "").lower()
    # `None` is a filter that is off, so `x in (None, value)` reads "off, or equal".
    matches = [match for match in visible
               if needle in f"{match.job.title} {match.job.company or ''} {match.job.url}".lower()
               and remote in (None, bool(match.job.is_remote))
               and job in (None, match.job.id)
               and (cutoff is None or _when(match) >= cutoff)
               and source in (None, match.job.source)
               and applied in (None, match.job.id in applied_at)
               and made in (None, match.job.id in made_for)]
    if sort == "newest":
        matches.sort(key=_when, reverse=True)
    page = matches[offset:offset + limit]
    documents = for_jobs(db, user_id, [match.job.id for match in page])
    boards = Counter(match.job.source for match in visible)
    return Feed(total=len(matches),
                jobs=[_row(match, applied_at.get(match.job.id), documents.get(match.job.id, []))
                      for match in page],
                sources=[FeedSource(name=name, jobs=count) for name, count in boards.most_common()])


def _when(match: Match) -> datetime:
    """When a job appeared: its posting date where the board gives one, else when it was found."""
    return match.job.posted_at or match.job.first_seen_at


def _row(match: Match, applied_at: datetime | None, documents: list[dict]) -> FeedJob:
    """One job, without its description: the table shows none of it but the salary."""
    job = match.job
    return FeedJob(id=job.id, title=job.title, company=job.company, location=job.location,
                   is_remote=job.is_remote, url=job.url, source=job.source, score=match.score,
                   words=list(match.words), posted_at=job.posted_at,
                   first_seen_at=job.first_seen_at, salary=salary.extract(job.description),
                   applied_at=applied_at, documents=[job_document(row) for row in documents])


class AppliedJob(BaseModel):
    id: int
    title: str
    company: str | None
    location: str | None
    url: str
    applied_at: datetime
    documents: list[JobDocument]


@router.get("/applied")
def applied(db: Database = Depends(get_db),
            user_id: _uuid.UUID = Depends(current_user)) -> list[AppliedJob]:
    """Every job this person said they applied to, the latest first, whether or not it still matches
    their words. Retention keeps these postings (`libs.corpus.archive.ACTED_ON`)."""
    rows = list(db[UserJobState.COLLECTION].find(
        {"user_id": user_id, "applied_at": {"$ne": None}}, {"job_id": 1, "applied_at": 1},
        sort=[("applied_at", -1)]))
    jobs = {doc["_id"]: doc for doc in db[CorpusJob.COLLECTION].find(
        {"_id": {"$in": [row["job_id"] for row in rows]}},
        {"title": 1, "company": 1, "location": 1, "url": 1})}
    documents = for_jobs(db, user_id, list(jobs))
    return [AppliedJob(id=row["job_id"], title=job["title"], company=job.get("company"),
                       location=job.get("location"), url=job["url"], applied_at=row["applied_at"],
                       documents=[job_document(each) for each in documents.get(row["job_id"], [])])
            for row in rows if (job := jobs.get(row["job_id"]))]


class JobMarks(BaseModel):
    job_id: int
    applied: bool
    hidden: bool


# "I applied" and "Hide" — the only two things a person records about a job, and nothing else does:
# making a document is not applying, and the model that read builds as applications is switched off.
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
