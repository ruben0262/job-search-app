"""The front page and the jobs page. Both ask `libs.matching`, the one answer to "which jobs"."""
from __future__ import annotations

import logging
import re
import uuid as _uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, ValidationError
from pymongo.database import Database

from libs import fit, prompts
from libs.corpus import salary
from libs.corpus.boards import apply_on, board_name
from libs.corpus.freshness import platform_windows, shelf_life
from libs.corpus.geo import CITIES, COUNTRY_LABELS
from libs.corpus.postings import paragraphs
from libs.corpus.repository import TYPED
from libs.db import get, now
from libs.db.models import AtsToken, BuiltDocument, CorpusJob, CountryCount, User, UserJobState
from libs.db.models.tenant import record_state
from libs.documents.saved import for_jobs
from libs.matching import Match, marks, norm, page, scan_for
from libs.membership import areas_of, choosing, delay_hours, has, is_admin, is_member, jobs_as_of
from libs.notify import reachable
from libs.obs import audit
from libs.onboarding import onboarding_state
from libs.settings import HARVEST_SOURCES, PLATFORM_TICK_MIN
from libs.tenant import applications
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


class PlanDashboard(BaseModel):
    """Between the email link and setting up: Free or the membership, picked first (owner, 2026-11-12), and
    nothing else of the site until it is (`libs.membership.choosing`)."""
    stage: Literal["plan"]
    is_admin: bool
    member: bool


class OnboardingDashboard(BaseModel):
    stage: Literal["onboarding"]
    # `onboarding_state(...).as_dict()`, in onboarding's own model: its pages are onboarding's to define.
    onboarding: OnboardingSteps
    is_admin: bool
    # On a paid plan, or given one (`libs.membership`); without one the site is theirs, each job a week late.
    member: bool
    # What their plan gives (`libs.membership.PLANS`): what a page shows or offers by.
    areas: list[str]


class JobFilters(BaseModel):
    """The Jobs page's filters, named as `/feed` takes them, kept for each person so the page opens as they
    left it, on any device (owner, 2026-11-07). A field left out is its default, which is what a new
    account sees."""
    q: str = Field("", max_length=200)
    remote: bool = False
    # "Last 24 hours" for everybody until they choose another (owner, 2026-11-07). None: "Any time", the
    # whole job shelf life.
    since_hours: int | None = Field(24, ge=1, le=24 * 60)
    source: str = Field("", max_length=60)
    country: str = Field("", max_length=2)
    city: str = Field("", max_length=40)
    sort: Literal["best", "newest", "arrived"] = "best"
    # False: only the jobs with no CV made yet. None: every job.
    made: bool | None = None
    word: list[str] = Field(default_factory=list, max_length=50)
    min_words: int = Field(1, ge=1, le=50)
    # Only the jobs whose match score shows this or more (`libs.fit`); None: every job.
    min_fit: int | None = Field(None, ge=1, le=10)


class ReadyDashboard(BaseModel):
    """What every signed-in page needs before it draws. No count of matches: the jobs page is the
    front page and counts its own list, and asking here as well was a second full match run on every
    load — /dashboard went from 150 ms to 13 ms (median, 2,000 rows) without it."""
    stage: Literal["ready"]
    is_admin: bool
    # On a paid plan, or given one (`libs.membership`); without one the site is theirs, each job a week late.
    member: bool
    # What their plan gives (`libs.membership.PLANS`): what a page shows or offers by.
    areas: list[str]
    # How many jobs they said they applied to — moved only by "Mark as applied".
    applied: int
    banners: list[DashboardBanner]
    # What the Jobs page opens with: theirs as they left it, or the defaults.
    job_filters: JobFilters


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
    # What its button says after "Apply on": the board's name, or "the job's site" for one somebody added.
    apply_on: str
    # The green words that matched, anywhere in the job: why it is on the page.
    words: list[str]
    posted_at: datetime | None
    first_seen_at: datetime | None
    # When its text was read, and so when it reached the list (`candidate_conditions`): what makes it
    # new. None only for a job somebody wrote in themselves with a short text.
    described_at: datetime | None
    salary: FeedSalary | None
    # The start of the description, whitespace folded, cut at a word: what the card shows. None until
    # the posting has been described.
    summary: str | None
    # Whether there is more of it than the summary, to fetch from `/jobs/{id}/description`.
    more: bool
    # The CVs and cover letters made for it, newest first: still downloadable after a refresh.
    documents: list[JobDocument]
    # They bookmarked it: it is under Bookmarks too.
    bookmarked: bool
    # How well it fits them, 1 to 10 (`libs.fit`), for a plan with match scores and documents to compare with;
    # None otherwise.
    fit: int | None = None


class FeedSource(BaseModel):
    name: str
    # The board's name as people write it: "LinkedIn", "No Fluff Jobs".
    label: str
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
    # Of `total`, how many reached the list after `found_after`; `None` when it was not asked. With `total`
    # beside it, the page tells the jobs that came from the ones that left meanwhile.
    arrived: int | None = None
    # How long a job stays on the page, in hours: the job shelf life set in Admin, and the longest a board
    # that posts less often keeps its jobs there (`freshness.shelf_life`). Said on the page.
    shelf_hours: int
    shelf_longest_hours: int
    # How long after it is found a job reaches this list, in hours, as their plan waits
    # (`libs.membership.delay_hours`): none for a member. "Posted" offers only the times longer than it.
    delay_hours: int = 0
    # More of their jobs matched than one read takes in (`libs.matching.scan_for`): the list is the newest
    # of them, and the page says so rather than call it all of them.
    cut: bool
    # This account has match scores: its plan has them, and it has documents to compare with.
    match_scores: bool = False


@router.get("/dashboard")
def dashboard(db: Database = Depends(get_db), user_id: _uuid.UUID = Depends(current_user)
              ) -> PlanDashboard | OnboardingDashboard | ReadyDashboard:
    """Everything the front page shows, in one call. Opening the site is being online: the time is kept to
    the minute, for the admin's account page."""
    user = get(db, User, user_id)
    db[User.COLLECTION].update_one(
        {"_id": user_id, "$or": [{"last_active_at": None},
                                 {"last_active_at": {"$lt": now() - timedelta(minutes=1)}}]},
        {"$set": {"last_active_at": now()}})
    member = is_member(db, user_id)
    if choosing(user, member):
        return PlanDashboard(stage="plan", is_admin=is_admin(user), member=member)
    state = onboarding_state(db, user_id)
    # Finished once is not enough: a search saved before the minimums (owner, 2026-11-04) is finished on
    # its page, since without a country no job comes in for it.
    unfinished = {page.key for page in state.pages if not page.complete}
    if user.onboarding_completed_at is None or "review" in unfinished:
        # Nothing below is meaningful yet, and empty panels read as a broken product.
        return OnboardingDashboard(stage="onboarding", is_admin=is_admin(user), member=member,
                                   areas=areas_of(db, user_id), onboarding=OnboardingSteps(**state.as_dict()))

    _, applied = marks(db, user_id)
    return ReadyDashboard(stage="ready", is_admin=is_admin(user), member=member, areas=areas_of(db, user_id),
                          applied=len(applied), banners=_banners(db, user, member),
                          job_filters=_job_filters(user))


def _job_filters(user: User) -> JobFilters:
    """The filters they left the Jobs page with, or the defaults. Saved ones that no longer read, after a
    filter changed shape, are the defaults, and the log says so."""
    try:
        return JobFilters.model_validate(user.job_filters or {})
    except ValidationError:
        logger.warning("jobs page: %s's saved filters no longer read, so the defaults instead", user.id)
        return JobFilters()


@router.put("/feed/filters")
def save_job_filters(filters: JobFilters, db: Database = Depends(get_db),
                     user_id: _uuid.UUID = Depends(current_user)) -> JobFilters:
    """The Jobs page's filters as they are now, to open with next time. Sent as they change, so a closed
    tab loses nothing."""
    db[User.COLLECTION].update_one({"_id": user_id}, {"$set": {"job_filters": filters.model_dump()}})
    return filters


class AtsMeasured(BaseModel):
    """How many CVs passed every ATS check in our own tests, with what was tested and when: said on the
    landing page with all of it (owner, 2026-11-12: sourced figures only; 2026-11-15: on the site if above
    95.3%)."""
    passed: int
    of: int
    checks: int
    looks: int
    month: str
    # The version of `write_cv.md` that wrote the CVs measured (`libs.prompts.version`).
    prompt: str


# Measured 2026-11-16 (CHANGELOG): 36 CVs, nine made-up people and two postings each, 18 written by Claude
# Haiku 5.5 and 18 by GPT, each drawn in all eight looks and read back by two PDF readers, against the eight
# checks in `scripts/cvlab/run.py::ats_checks`. **A figure about one wording**: `stats` serves it only while
# that wording is the live one, so a changed prompt takes it off the page until it is measured again and
# this line is rewritten (`tests/test_dashboard.py` holds the two together).
ATS_MEASURED = AtsMeasured(passed=285, of=288, checks=8, looks=8, month="November 2026",
                           prompt="6fcbbabfca70")


class Reach(BaseModel):
    """How much the platform reads, counted from what it reads. The landing page typed its own numbers
    and still said 25 sources and ~940 company boards after both had changed (2026-11-06)."""
    # The one source list (`HARVEST_SOURCES`), LinkedIn included; two of them run only with their key.
    sources: int
    # Company boards still answering: a dead one is kept so it is not found again, and not counted.
    company_boards: int
    every_minutes: int
    # New jobs found in the last seven days, counted as the admin's chart counts them (`CountryCount`, one
    # row a run a country): what was found, never a typed number.
    jobs_week: int
    # What our own tests measured of the CVs made here (`ATS_MEASURED`), or None while the CV prompt is not
    # the one they measured: the page then says no figure rather than an old one.
    ats: AtsMeasured | None


@router.get("/stats", response_model=Reach)
def stats(db: Database = Depends(get_db)) -> Reach:
    """Public: the landing page asks before anybody has an account."""
    week = db[CountryCount.COLLECTION].aggregate([
        {"$match": {"hour": {"$gte": now() - timedelta(days=7)}}},
        {"$group": {"_id": None, "jobs": {"$sum": "$jobs"}}}])
    return Reach(sources=len(HARVEST_SOURCES), every_minutes=PLATFORM_TICK_MIN,
                 company_boards=db[AtsToken.COLLECTION].count_documents({"dead_at": None}),
                 jobs_week=next(week, {}).get("jobs", 0),
                 ats=ATS_MEASURED if prompts.version("write_cv.md") == ATS_MEASURED.prompt else None)




def _span(hours: int) -> str:
    """A time as the pages say it (`web/src/time.ts`, `span`): "36 hours", "7 days"."""
    return f"{hours} hours" if hours < 48 or hours % 24 else f"{hours // 24} days"


def _banners(db: Database, user: User, member: bool) -> list[DashboardBanner]:
    """What the jobs page says above the list, one thing at a time. Alerts are optional, so having
    none is a quiet offer rather than a warning — and a Telegram that stopped is a button to press. A free
    account is told first what it is looking at, and what a membership adds: alerts are paid plans' too."""
    if not member:
        delay = delay_hours(db, user)
        text = "Alerts, match scores, CVs and cover letters come with a membership."
        if delay:
            text = (f"You see jobs that are {_span(delay)} or older. Members see each one the moment we find "
                    "it, and apply among the first.")
        return [DashboardBanner(level="info", key="free", text=text)]
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
         min_fit: int | None = Query(None, ge=1, le=10),
         background: BackgroundTasks,
         found_after: datetime | None = None,
         sort: Literal["best", "newest", "arrived"] = "best") -> Feed:
    """This person's matches, a page at a time: the most of their words first, the newest posted
    first, or the ones that arrived here last first:
    searched, remote only, posted lately, from one board, in one country or one city, with a document
    made or not, having every word picked, or at least so many of their words — or one job, the one
    an alert's link names, which is either still in their list or not. `found_after` counts the jobs
    that reached the list since it was read (`arrived`), which is how the page learns how many new ones
    there are without reloading. A job reaches it when its text is read, not when it is found: a LinkedIn
    job found before the list was read and read after it is new to them.

    Counted on the whole list, not instead of it: the jobs that left meanwhile, past their shelf life,
    are the difference between the two lengths. Asked for the new ones alone, the page said "100 new
    jobs" and a refresh made the list 15 longer (owner, 2026-11-06).

    A job they skipped or applied to is gone from it for good: Applied lists the second."""
    # An account on a plan that waits sees a job that long after a member does (`libs.membership`), so a
    # "Posted" shorter than that would be empty: it is all of their list instead, as the page shows it.
    # New jobs are counted from when its list stands, the newest a job in it can have arrived.
    delay = delay_hours(db, user := get(db, User, user_id))
    behind = jobs_as_of(db, user, delay)
    if since_hours and since_hours <= delay:
        since_hours = None
    as_of = behind or now()
    shortest, longest = shelf_life(platform_windows(db))
    cutoff = now() - timedelta(hours=since_hours) if since_hours else None
    # Made by the database, the page and the counts beside the filters alike (`libs.matching.page`): only the
    # page leaves it. Reading the whole list here took 1,025 ms at 15,554 jobs and grew with the list
    # (2026-11-15). The read reaches as far back as asked, "Any time" being the longest shelf life
    # (`scan_for`); what the filters offer is counted inside the time asked, as the list is shown: "golang 5"
    # beside a list of 2 from the last day disagreed (2026-11-07).
    made_for = [] if made is None else db[BuiltDocument.COLLECTION].distinct("job_id", {"user_id": user_id})
    picked = sorted({norm(each) for each in word if each.strip()})
    wanted = [condition for on, condition in (
        (bool(q), {"$expr": {"$regexMatch": {
            "input": {"$concat": ["$title", " ", {"$ifNull": ["$company", ""]}, " ", "$url"]},
            "regex": re.escape(q or ""), "options": "i"}}}),
        (remote is not None, {"is_remote": True} if remote else {"is_remote": {"$ne": True}}),
        (job is not None, {"_id": job}),
        (source is not None, {"source": source}),
        (country is not None, {"country": country}),
        (city is not None, {"city": city}),
        (made is not None, {"_id": {"$in" if made else "$nin": made_for}}),
        (bool(picked), {"words": {"$all": picked}}),
        (min_words > 1, {"nwords": {"$gte": min_words}})) if on]
    # A time without a zone is UTC, as the database reads one when it does the comparing.
    since = found_after.replace(tzinfo=found_after.tzinfo or UTC) if found_after else None
    # Their match score, for a plan that has it (owner, 2026-11-16: Starter and Member): on each card, worked
    # out with the page; and a filter over the whole list, read from the scores kept for their profile as it
    # is, worked out there and then for a job with none (`libs.fit.at_least`). So it is right the moment their
    # documents change. With nothing kept for their profile, or a filter that found jobs not kept, their list
    # is kept after this answer (`fill`), which makes the next filter quick.
    mine = fit.profile(db, user) if has(db, user.id, "match_scores") else None
    kept = fit.kept(db, user_id, mine) if mine is not None and min_fit is not None else None
    if kept is not None:
        wanted.append(fit.at_least(mine, kept, min_fit))
    found = page(db, user, since=cutoff, scan=scan_for(since_hours or longest),
                 wanted={"$and": wanted} if wanted else {}, order=sort, offset=offset, limit=limit,
                 found_after=since, fit=None if mine is None else fit.scored(mine),
                 unkept=None if kept is None else fit.unkept(kept))
    if mine is not None and (found.live or not fit.any_kept(db, user_id, mine)):
        background.add_task(fit.fill, db, user_id)
    ids = [match.job.id for match in found.matches]
    # The text of the page's jobs only: matching never brings it back.
    texts = {row["_id"]: row.get("description") for row in db[CorpusJob.COLLECTION].find(
        {"_id": {"$in": ids}}, {"description": 1})}
    documents = for_jobs(db, user_id, ids)
    kept = bookmarked(db, user_id, ids)
    return Feed(total=found.total, arrived=found.arrived,
                jobs=[feed_row(match, texts.get(match.job.id), documents.get(match.job.id, []),
                               bookmarked=match.job.id in kept) for match in found.matches],
                sources=[FeedSource(name=name, label=board_name(name), jobs=count)
                         for name, count in found.sources],
                words=[FeedWord(name=name, jobs=count) for name, count in found.words],
                countries=[FeedPlace(value=code, label=COUNTRY_LABELS.get(code, code), jobs=count)
                           for code, count in found.countries],
                cities=[FeedPlace(value=slug, label=CITIES[slug].label if slug in CITIES else slug,
                                  jobs=count) for slug, count in found.cities],
                as_of=as_of, shelf_hours=shortest, shelf_longest_hours=longest, delay_hours=delay,
                cut=found.cut, match_scores=mine is not None)


def bookmarked(db: Database, user_id: _uuid.UUID, ids: list[int]) -> set[int]:
    """Which of these jobs this person bookmarked."""
    return set(db[UserJobState.COLLECTION].distinct(
        "job_id", {"user_id": user_id, "job_id": {"$in": ids}, "bookmarked_at": {"$ne": None}}))


def feed_row(match: Match, text: str | None, documents: list[dict], *,
             bookmarked: bool = False) -> FeedJob:
    """One job, with the start of its text and the salary in it, not the whole of it."""
    job = match.job
    return FeedJob(id=job.id, title=job.title, company=job.company, location=job.location,
                   country=job.country, is_remote=job.is_remote, url=job.url, source=job.source,
                   apply_on=apply_on(job.source), words=list(match.words), posted_at=job.posted_at,
                   first_seen_at=job.first_seen_at, described_at=job.described_at,
                   salary=salary.extract(text), **_summary(text),
                   documents=[job_document(row) for row in documents], bookmarked=bookmarked,
                   fit=None if match.fit is None else fit.shown(match.fit))


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
                    user_id: _uuid.UUID = Depends(current_user)) -> JobDescription:
    """A posting's whole description, for a card that was asked to show it: as stored, or in paragraphs
    if it was stored as one line (`paragraphs`)."""
    where: dict = {"_id": job_id}
    if (behind := jobs_as_of(db, get(db, User, user_id))) is not None:
        # Not the text of a job a waiting account's list does not show yet, asked for by its number.
        where["$or"] = [{"source": TYPED}, {"described_at": {"$lte": behind}}]
    found = db[CorpusJob.COLLECTION].find_one(where, {"description": 1})
    if not found or not found.get("description"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no description for that job")
    return JobDescription(text=paragraphs(found["description"]))


class Step(BaseModel):
    """One thing that happened after "Mark as applied", in the person's own words."""
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
    bookmarked: bool


# "Mark as applied" and "Skip" — the only two things a person records about what they did with a job, and
# nothing else does: making a document is not applying. Either takes the job off their jobs page for good.
# Skip is `hidden` here, as it was called before the page renamed it. A bookmark takes nothing off any
# page: it lists the job under Bookmarks as well (owner, 2026-11-15).
_MARKS = {"applied": "applied_at", "hidden": "dismissed_at", "bookmarked": "bookmarked_at"}


@router.put("/jobs/{job_id}/{mark}", response_model=JobMarks)
@router.delete("/jobs/{job_id}/{mark}", response_model=JobMarks)
def mark_job(job_id: int, mark: Literal["applied", "hidden", "bookmarked"], request: Request,
             db: Database = Depends(get_db),
             user_id: _uuid.UUID = Depends(current_user)) -> JobMarks:
    """PUT records the mark, DELETE takes it back. Only ever this person's own row."""
    where: dict = {"_id": job_id}
    if request.method == "PUT" and (behind := jobs_as_of(db, get(db, User, user_id))) is not None:
        # A waiting account marks only what its list shows: Applied would show the rest of a newer job.
        where["$or"] = [{"source": TYPED}, {"described_at": {"$lte": behind}}]
    if not db[CorpusJob.COLLECTION].count_documents(where, limit=1):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such job")
    state = record_state(db, user_id, job_id, **{_MARKS[mark]: now() if request.method == "PUT" else None})
    audit(logger, "job.marked", user=user_id, job=job_id, mark=mark, on=request.method == "PUT")
    return JobMarks(job_id=job_id, applied=state.applied_at is not None,
                    hidden=state.dismissed_at is not None, bookmarked=state.bookmarked_at is not None)
