"""The front page and the jobs page. Both ask `libs.matching`, the one answer to "which jobs"."""
from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from libs.corpus import salary
from libs.db.models import CorpusJob, User, UserJobState
from libs.matching import Match, matching_jobs
from libs.onboarding import onboarding_state
from services.api.admin_routes import admin_emails
from services.api.deps import current_user, get_db

router = APIRouter(tags=["dashboard"])

FEED_PAGE = 25


class DashboardBanner(BaseModel):
    level: Literal["error", "warning", "info"]
    key: str
    text: str


class OnboardingDashboard(BaseModel):
    stage: Literal["onboarding"]
    # `onboarding_state(...).as_dict()`, passed through: its pages are onboarding's to define.
    onboarding: dict
    is_admin: bool


class ReadyDashboard(BaseModel):
    stage: Literal["ready"]
    is_admin: bool
    telegram: str
    # The jobs page's unfiltered total, so the two pages cannot show different numbers.
    matching: int
    applied: int
    banners: list[DashboardBanner]


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


class Feed(BaseModel):
    total: int
    # Every source among this person's matches, so the dropdown offers only what can be picked.
    sources: list[str]
    jobs: list[FeedJob]


def _is_admin(user: User) -> bool:
    """The same rule `require_admin` enforces, in one place."""
    return bool(user.is_admin) or user.email.lower() in admin_emails()


def _marks(db: Session, user_id: _uuid.UUID) -> tuple[set[int], dict[int, datetime]]:
    """The jobs this person dismissed, and when they applied to which."""
    rows = db.execute(
        select(UserJobState.job_id, UserJobState.dismissed_at, UserJobState.applied_at)
        .where(UserJobState.user_id == user_id,
               UserJobState.dismissed_at.isnot(None) | UserJobState.applied_at.isnot(None))).all()
    return ({job for job, dismissed, _ in rows if dismissed},
            {job: applied for job, _, applied in rows if applied})


def _visible(db: Session, user: User, dismissed: set[int]) -> list[Match]:
    """This person's matches, minus the ones they dismissed."""
    return [match for match in matching_jobs(db, user) if match.job.id not in dismissed]


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), user_id: _uuid.UUID = Depends(current_user)
              ) -> OnboardingDashboard | ReadyDashboard:
    """Everything the front page shows, in one call."""
    user = db.get(User, user_id)
    if user.onboarding_completed_at is None:
        # Nothing below is meaningful yet, and empty panels read as a broken product.
        return OnboardingDashboard(stage="onboarding", is_admin=_is_admin(user),
                                   onboarding=onboarding_state(db, user_id).as_dict())

    dismissed, applied = _marks(db, user_id)
    return ReadyDashboard(stage="ready", is_admin=_is_admin(user), telegram=user.telegram_status,
                          matching=len(_visible(db, user, dismissed)), applied=len(applied),
                          banners=_banners(user))


def _banners(user: User) -> list[DashboardBanner]:
    """The health warnings of `OPERATIONS_PLAN.md` §4b — failures the user can fix, said plainly."""
    banners = []
    if user.telegram_status == "broken":
        banners.append(DashboardBanner(level="error", key="telegram_broken",
                              text="Your bot can no longer message you — reconnect it in settings."))
    elif user.telegram_status != "connected":
        banners.append(DashboardBanner(level="warning", key="telegram_unconfigured",
                              text="Telegram is not connected, so nothing is being delivered."))
    if not user.active:
        banners.append(DashboardBanner(level="info", key="paused",
                              text="Your feed is paused. Turn it on to start receiving jobs."))
    return banners


# What each column sorts by. A missing value sorts last whichever way the column is turned.
_COLUMNS = {"fetched": lambda job: job.first_seen_at,
            "title": lambda job: job.title.lower(),
            "company": lambda job: job.company.lower() if job.company else None}


@router.get("/feed")
def feed(*, db: Session = Depends(get_db), user_id: _uuid.UUID = Depends(current_user),
         limit: int = Query(FEED_PAGE, ge=1, le=100),
         offset: int = Query(0, ge=0),
         q: str | None = Query(None, max_length=200),
         source: str | None = Query(None, max_length=64),
         remote: bool | None = None,
         applied: bool | None = None,
         max_age_days: int | None = Query(None, ge=1, le=365),
         sort: Literal["score", "fetched", "title", "company"] = "score",
         direction: Literal["asc", "desc"] = "desc") -> Feed:
    """This person's matches, a page at a time, with the filters the table offers."""
    dismissed, applied_at = _marks(db, user_id)
    matches = _visible(db, db.get(User, user_id), dismissed)
    sources = sorted({match.job.source for match in matches})

    needle = (q or "").lower()
    oldest = datetime.now(UTC) - timedelta(days=max_age_days) if max_age_days else None
    # `None` is a filter that is off, so `x in (None, value)` reads "off, or equal".
    matches = [match for match in matches
               if needle in f"{match.job.title} {match.job.company or ''} {match.job.url}".lower()
               and source in (None, match.job.source)
               and remote in (None, bool(match.job.is_remote))
               and applied in (None, match.job.id in applied_at)
               # An undated posting is kept: no date is not evidence of an old one.
               and not (oldest and match.job.posted_at and match.job.posted_at < oldest)]

    if sort == "score":
        # Already best first, then newest first: `matching_jobs`' own order, not a second one.
        if direction == "asc":
            matches.reverse()
    else:
        value = _COLUMNS[sort]
        known = sorted((match for match in matches if value(match.job) is not None),
                       key=lambda match: value(match.job), reverse=direction == "desc")
        matches = known + [match for match in matches if value(match.job) is None]

    return Feed(total=len(matches), sources=sources,
                jobs=[_row(match, applied_at.get(match.job.id))
                      for match in matches[offset:offset + limit]])


def _row(match: Match, applied_at: datetime | None) -> FeedJob:
    """One job, without its description: the table shows none of it but the salary."""
    job = match.job
    return FeedJob(id=job.id, title=job.title, company=job.company, location=job.location,
                   is_remote=job.is_remote, url=job.url, source=job.source, score=match.score,
                   words=list(match.words), posted_at=job.posted_at,
                   first_seen_at=job.first_seen_at, salary=salary.extract(job.description),
                   applied_at=applied_at)


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
             db: Session = Depends(get_db),
             user_id: _uuid.UUID = Depends(current_user)) -> JobMarks:
    """PUT records the mark, DELETE takes it back. Only ever this person's own row."""
    if db.get(CorpusJob, job_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such job")
    state = db.get(UserJobState, (user_id, job_id)) or UserJobState(user_id=user_id, job_id=job_id)
    db.add(state)
    setattr(state, _MARKS[mark], func.now() if request.method == "PUT" else None)
    db.flush()
    return JobMarks(job_id=job_id, applied=state.applied_at is not None,
                    hidden=state.dismissed_at is not None)
