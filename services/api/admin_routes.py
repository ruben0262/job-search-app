"""The operator's read-only view of the platform."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid as _uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from pymongo.database import Database

from libs.contracts.config import WORKPLACE_NAMES, location_search_name, migrate_config
from libs.db import delete_account, find, find_one, get, insert
from libs.db.models import (
    AtsToken,
    BuiltDocument,
    CorpusJob,
    OperatorKeyword,
    PushSubscription,
    QueueJob,
    User,
    UserConfig,
    UserDocument,
    UserJobState,
)
from libs.documents.read import LANGUAGE_NAMES
from libs.harvest import distinct_keywords
from libs.limits import all_for, describe, set_limit
from libs.matching import green_words, their_jobs
from libs.obs import audit
from libs.ops import evaluate, platform_health, render
from libs.ops.latency import (
    latency_report,
    pipeline_latency,
    stage_detail,
)
from libs.ops.pipeline import pipeline
from libs.settings import (
    DEFAULT_SOURCE_MIN_AGE_HOURS,
    DEFAULTS,
    HARVEST_SOURCES,
    QUERY_TAKING_SOURCES,
    all_settings,
    ats_companies_per_cycle,
    freshness_hours,
    pages_per_cycle,
    put,
    queries_per_cycle,
    results_per_query,
    snapshot,
    source_min_ages,
)
from libs.tenant import applications
from libs.tenant.repository import TenantRepository
from services.api.deps import current_user, get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])


def admin_emails() -> set[str]:
    """Read per call rather than cached at import: a restart is a slow way to fix an env var."""
    raw = os.environ.get("ADMIN_EMAILS", "")
    return {entry.strip().lower() for entry in raw.split(",") if entry.strip()}


def require_admin(db: Database = Depends(get_db),
                  user_id: _uuid.UUID = Depends(current_user)) -> User:
    """404, not 403."""
    user = get(db, User, user_id)
    allowed = admin_emails()
    # The env var or the row.
    granted = user is not None and (user.is_admin or user.email.lower() in allowed)
    if not granted:
        logger.warning("admin: refused %s", user_id)
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")
    return user


@router.get("/health")
def health(db: Database = Depends(get_db), admin: User = Depends(require_admin)) -> dict:
    """Every number, plus the conditions that currently need a person."""
    state = platform_health(db)
    alerts = evaluate(state)
    audit(logger, "admin.health", user=admin.id, alerts=len(alerts))
    return {
        **state.as_dict(),
        # How long a posting takes to reach somebody.
        "latency": pipeline_latency(db),
        "alerts": [
            {"severity": alert.severity, "key": alert.key, "text": alert.text,
             "action": alert.action, "user_email": alert.user_email}
            for alert in alerts],
        "summary": render(alerts),
    }


class OutcomeRow(BaseModel):
    """How far a group of applications got; a stage counts every one that reached it or went past it."""
    group: str
    applied: int
    interview: int
    offer: int
    hired: int
    rejected: int


@router.get("/outcomes")
def outcomes(db: Database = Depends(get_db), admin: User = Depends(require_admin)) -> list[OutcomeRow]:
    """What people say happened after they applied, by the CV they sent (`libs.tenant.applications`)."""
    return [OutcomeRow(**row) for row in applications.outcomes(db)]


@router.get("/users")
def users(db: Database = Depends(get_db), admin: User = Depends(require_admin)) -> dict:
    """The account list, with no credential material of any kind."""
    audit(logger, "admin.users", user=admin.id)
    allowed = admin_emails()
    return {
        "users": [
            {
                "id": str(row.id),
                "email": row.email,
                "status": row.status,
                "active": row.active,
                "telegram": row.telegram_status,
                # Effective access, not just the column.
                "is_admin": row.is_admin or row.email.lower() in allowed,
                # Whether the flag can be changed here at all.
                "admin_locked": row.email.lower() in allowed,
                "onboarded": row.onboarding_completed_at is not None,
                "processed_at": row.processed_at.isoformat() if row.processed_at else None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                # The whole of account lifecycle now: an operator reads this and decides whether to
                # delete. Nothing acts on it automatically (ROADMAP 6c).
                "last_active_at": (row.last_active_at.isoformat()
                                   if row.last_active_at else None),
            }
            for row in find(db, User, {}, sort=[("created_at", 1)])],
    }


class AdminAccount(BaseModel):
    id: str
    email: str
    status: str
    active: bool
    telegram: str
    onboarded: bool
    created_at: datetime | None
    onboarding_completed_at: datetime | None
    # When they last opened the site, to the minute (`/dashboard`).
    last_active_at: datetime | None


class AdminUsage(BaseModel):
    """What an account does with the site, for working out what to improve."""
    # What their Jobs page lists now, and how many of those came in since they were last on the site.
    jobs: int
    new_jobs: int
    applied: int
    skipped: int
    # Sent to them by an alert.
    delivered: int
    # CVs and cover letters made for a job.
    made: int
    cvs: int
    cvs_in_use: int
    cover_letters: int
    green_words: list[str]
    red_words: list[str]
    countries: list[str]
    workplaces: list[str]
    languages: list[str]
    # How they hear of new jobs.
    alerts: list[str]


class AdminAccountDetail(BaseModel):
    user: AdminAccount
    usage: AdminUsage
    # `None` is unlimited and is a real value, not a missing one.
    limits: dict[str, int | None]


@router.get("/users/{user_id}")
def user_detail(user_id: _uuid.UUID, db: Database = Depends(get_db),
                admin: User = Depends(require_admin)) -> AdminAccountDetail:
    """One account: who it is, when it was last here, and what it does with the site."""
    user = get(db, User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such user")

    audit(logger, "admin.user_detail", user=admin.id, subject=user_id)
    return AdminAccountDetail(
        user=AdminAccount(
            id=str(user.id), email=user.email, status=user.status, active=user.active,
            telegram=user.telegram_status, onboarded=user.onboarding_completed_at is not None,
            created_at=user.created_at, onboarding_completed_at=user.onboarding_completed_at,
            last_active_at=user.last_active_at),
        usage=_usage(db, user),
        limits=all_for(user))


def _usage(db: Database, user: User) -> AdminUsage:
    jobs = their_jobs(db, user)
    since = user.last_active_at
    config = migrate_config(TenantRepository(db).get_config(user.id) or {})
    sections = config.get("sections") or []

    def each(field: str) -> list[str]:
        return list(dict.fromkeys(value for section in sections for value in section.get(field) or []))

    def count(collection: str, **where) -> int:
        return db[collection].count_documents({"user_id": user.id, **where})

    documents = list(db[UserDocument.COLLECTION].find({"user_id": user.id}, {"kind": 1, "in_use": 1}))
    cvs = [row for row in documents if row["kind"] == "cv"]
    alerts = [name for name, on in (("email", user.email_alerts),
                                    ("Telegram", user.telegram_status == "connected"),
                                    ("browser", count(PushSubscription.COLLECTION) > 0)) if on]
    return AdminUsage(
        jobs=len(jobs), new_jobs=sum(1 for match in jobs if since and match.job.first_seen_at > since),
        applied=count(UserJobState.COLLECTION, applied_at={"$ne": None}),
        skipped=count(UserJobState.COLLECTION, dismissed_at={"$ne": None}),
        delivered=count(UserJobState.COLLECTION, notified_at={"$ne": None}),
        made=count(BuiltDocument.COLLECTION),
        cvs=len(cvs), cvs_in_use=sum(1 for row in cvs if row.get("in_use", True)),
        cover_letters=sum(1 for row in documents if row["kind"] == "cover_letter"),
        green_words=green_words(config), red_words=each("exclude_keywords"),
        countries=[location_search_name(slug) for slug in each("locations")],
        workplaces=[WORKPLACE_NAMES.get(way, (way,))[0] for way in each("workplaces")],
        languages=[LANGUAGE_NAMES.get(code, (code,))[0] for code in config.get("languages") or []],
        alerts=alerts)


class SettingPatch(BaseModel):
    value: dict | list | int | str


@router.get("/settings")
def read_settings(db: Database = Depends(get_db), admin: User = Depends(require_admin)) -> dict:
    """What the operator controls, with the defaults filled in for anything unset."""
    audit(logger, "admin.settings_read", user=admin.id)
    return {"settings": all_settings(db), "defaults": DEFAULTS}


@router.put("/settings/{key}")
def write_setting(key: str, body: SettingPatch, db: Database = Depends(get_db),
                  admin: User = Depends(require_admin)) -> dict:
    """Set one."""
    try:
        put(db, key, body.value, by=admin.email)
    except KeyError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    audit(logger, "admin.settings_write", user=admin.id, setting=key)
    return {"status": "ok", "settings": all_settings(db)}


class AdminFlag(BaseModel):
    is_admin: bool


@router.put("/users/{user_id}/admin")
def set_admin(user_id: _uuid.UUID, body: AdminFlag, db: Database = Depends(get_db),
              admin: User = Depends(require_admin)) -> dict:
    """Grant or revoke operator access."""
    if user_id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="You cannot change your own operator access.")

    target = get(db, User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such user")

    db[User.COLLECTION].update_one({"_id": user_id}, {"$set": {"is_admin": body.is_admin}})
    audit(logger, "admin.access_changed", user=admin.id, subject=user_id, granted=body.is_admin)
    return {"status": "ok", "id": str(user_id), "is_admin": body.is_admin}


class DeleteConfirmation(BaseModel):
    # The typed address, which must match the account being deleted.
    email: str


@router.delete("/users/{user_id}")
def delete_user(user_id: _uuid.UUID, body: DeleteConfirmation, db: Database = Depends(get_db),
                admin: User = Depends(require_admin)) -> dict:
    """Delete an account and everything belonging to it, immediately and permanently."""
    if user_id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="You cannot delete your own account from here.")

    target = get(db, User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such user")

    if body.email.strip().lower() != target.email.lower():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="That address does not match the account. Nothing was deleted.")

    email = target.email
    # Credentials and the key that reads them included: both are this account's documents.
    delete_account(db, user_id)
    audit(logger, "admin.user_deleted", user=admin.id, subject=user_id)
    logger.warning("admin: %s deleted account %s (%s)", admin.id, user_id, email)
    return {"status": "deleted", "id": str(user_id)}


@router.get("/sources")
def sources(db: Database = Depends(get_db), admin: User = Depends(require_admin)) -> dict:
    """Every source the harvester collects from, with the freshness window each one is using."""
    # One number per query-taking source: a source is asked once per distinct keyword.
    wanted = len(distinct_keywords(TenantRepository(db).active_configs()))
    asked = QUERY_TAKING_SOURCES | {"linkedin"}
    # Every setting below, from one read of them (`settings.snapshot`).
    stored = snapshot(db)
    floors = source_min_ages(stored)
    return {
        "sources": [{"name": name,
                     "hours": freshness_hours(stored, name),
                     # Its twin, shown beside it: `hours` is how far back the source is asked,
                     # this is how long what it returned stays deliverable. They were set in two
                     # different places, and only this one had no control at all — so widening a
                     # source's harvest window did nothing an operator could see if the floor
                     # below was discarding the result.
                     "min_age_hours": floors.get(name, DEFAULT_SOURCE_MIN_AGE_HOURS),
                     "queries_per_cycle": queries_per_cycle(stored, name),
                     # `None` where the source has no result cap to set at all.
                     "results_per_query": results_per_query(stored, name),
                     # `None` for a source that fetches one response rather than walking pages.
                     "pages_per_cycle": pages_per_cycle(stored, name),
                     "wanted": wanted if name in asked else 0}
                    for name in HARVEST_SOURCES],
        # The ATS ceiling, with the number it is a ceiling on.
        "ats": {
            "companies_per_cycle": ats_companies_per_cycle(stored),
            "companies_held": db[AtsToken.COLLECTION].count_documents({}),
        },
    }


class LimitPatch(BaseModel):
    # `None` is unlimited and is a real, storable answer — not a missing field.
    value: int | None = None


@router.get("/limits")
def limit_catalogue(admin: User = Depends(require_admin)) -> dict:
    """What can be set, what it defaults to, and what each one means."""
    return {"limits": describe()}


@router.put("/users/{user_id}/limits/{key}")
def set_user_limit(user_id: _uuid.UUID, key: str, body: LimitPatch,
                   db: Database = Depends(get_db),
                   admin: User = Depends(require_admin)) -> dict:
    """Set one ceiling on one account, or clear it to unlimited."""
    target = get(db, User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such user")

    try:
        set_limit(db, user_id, key, body.value)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    audit(logger, "admin.limit_set", user=admin.id, subject=user_id, limit=key, value=body.value)
    return {"status": "ok", "limits": all_for(get(db, User, user_id))}

# What an operator can page through at once.
CORPUS_PAGE = 50


@router.get("/corpus")
def corpus(db: Database = Depends(get_db), admin: User = Depends(require_admin), *,
           q: str = "", source: str = "", days: int = 30,
           offset: int = 0) -> dict:
    """Search the shared corpus."""
    window = datetime.now(UTC) - timedelta(days=max(days, 1))
    where: dict = {"first_seen_at": {"$gte": window}}
    if q.strip():
        needle = {"$regex": re.escape(q.strip()), "$options": "i"}
        where["$or"] = [{"title": needle}, {"company": needle}]
    if source.strip():
        where["source"] = source.strip()

    total = db[CorpusJob.COLLECTION].count_documents(where)
    rows = find(db, CorpusJob, where, sort=[("first_seen_at", -1), ("_id", -1)],
                skip=max(offset, 0), limit=CORPUS_PAGE)
    by_source = [(row["_id"], row["count"]) for row in db[CorpusJob.COLLECTION].aggregate([
        {"$match": {"first_seen_at": {"$gte": window}}},
        {"$group": {"_id": "$source", "count": {"$sum": 1}}}, {"$sort": {"count": -1}}])]

    audit(logger, "admin.corpus", user=admin.id, q=q[:60], total=total)
    return {
        "total": total,
        "offset": max(offset, 0),
        "page_size": CORPUS_PAGE,
        "sources": [{"source": name or "unknown", "count": count} for name, count in by_source],
        "jobs": [
            {"id": row.id, "title": row.title, "company": row.company,
             "location": row.location, "source": row.source, "is_remote": row.is_remote,
             "described": bool(row.description and len(row.description) > 400),
             "url": row.url,
             "first_seen_at": row.first_seen_at.isoformat() if row.first_seen_at else None,
             "posted_at": row.posted_at.isoformat() if row.posted_at else None}
            for row in rows],
    }


class KeywordBody(BaseModel):
    keyword: str = Field(min_length=2, max_length=100)
    # Absent means every location the platform already searches.
    location: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=200)


@router.get("/keywords")
def keywords(db: Database = Depends(get_db), admin: User = Depends(require_admin)) -> dict:
    """Every term the harvest currently searches, and who it belongs to."""
    injected = find(db, OperatorKeyword, {}, sort=[("keyword", 1)])

    owned: dict[str, set[str]] = {}
    rows = [(row["user_id"], row["config"])
            for row in db[UserConfig.COLLECTION].find({}, {"user_id": 1, "config": 1})]
    emails = {row["_id"]: row["email"] for row in db[User.COLLECTION].find({}, {"email": 1})}
    for user_id, config in rows:
        for section in (config or {}).get("sections") or []:
            for term in section.get("keywords") or []:
                owned.setdefault(str(term).strip().lower(), set()).add(
                    emails.get(user_id, "unknown"))

    return {
        "operator": [
            {"id": row.id, "keyword": row.keyword, "location": row.location, "note": row.note,
             "created_by": row.created_by,
             "created_at": row.created_at.isoformat() if row.created_at else None}
            for row in injected],
        # Read-only in the payload too, so a client cannot offer a delete the server would refuse.
        "users": [{"keyword": term, "accounts": sorted(who), "deletable": False}
                  for term, who in sorted(owned.items())],
    }


@router.post("/keywords", status_code=status.HTTP_201_CREATED)
def add_keyword(body: KeywordBody, db: Database = Depends(get_db),
                admin: User = Depends(require_admin)) -> dict:
    """Inject a term into the harvest, to fill the corpus ahead of the users who will need it."""
    term = " ".join(body.keyword.split()).lower()
    place = (body.location or "").strip() or None
    existing = find_one(db, OperatorKeyword, {"keyword": term, "location": place})
    if existing is not None:
        # Idempotent: adding the same term twice is a person being unsure, not a request for two.
        return {"id": existing.id, "keyword": existing.keyword, "location": existing.location,
                "created": False}

    row = insert(db, OperatorKeyword(keyword=term, location=place, note=(body.note or "").strip() or None,
                                     created_by=admin.email))
    audit(logger, "admin.keyword_added", user=admin.id, keyword=term, location=place)
    return {"id": row.id, "keyword": row.keyword, "location": row.location, "created": True}


@router.delete("/keywords/{keyword_id}")
def remove_keyword(keyword_id: int, db: Database = Depends(get_db),
                   admin: User = Depends(require_admin)) -> dict:
    """Remove an injected term."""
    row = get(db, OperatorKeyword, keyword_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such keyword")
    db[OperatorKeyword.COLLECTION].delete_one({"_id": keyword_id})
    audit(logger, "admin.keyword_removed", user=admin.id, keyword=row.keyword)
    return {"status": "removed", "keyword": row.keyword}

@router.delete("/queue/dead")
def discard_dead_letters(db: Database = Depends(get_db), admin: User = Depends(require_admin), *,
                         lane: str | None = None) -> dict:
    """Clear dead-lettered jobs an operator has read and dealt with."""
    where = {"state": "dead", **({"lane": lane} if lane else {})}
    rows = find(db, QueueJob, where)
    for row in rows:
        logger.warning("admin: discarding dead %s job %s for user %s — last error: %s",
                       row.lane, row.id, row.user_id, (row.last_error or "")[:200])
    db[QueueJob.COLLECTION].delete_many({"_id": {"$in": [row.id for row in rows]}})

    audit(logger, "admin.dead_discarded", user=admin.id, lane=lane or "all", count=len(rows))
    return {"status": "discarded", "count": len(rows), "lane": lane}


@router.get("/pipeline")
def pipeline_shape(db: Database = Depends(get_db), admin: User = Depends(require_admin), *,
                   hours: int = 24) -> dict:
    """Where the jobs are right now, stage by stage."""
    return pipeline(db, hours=max(1, min(hours, 168)))


# How often the live view recomputes.
STREAM_INTERVAL_SECONDS = 1.0

# A stream is an authorisation held open.
STREAM_MAX_SECONDS = 300


@router.get("/pipeline/stream")
async def pipeline_stream(request: Request, db: Database = Depends(get_db),
                          admin: User = Depends(require_admin), *, hours: int = 24) -> Response:
    """The same numbers as `/pipeline`, pushed once a second."""
    window = max(1, min(hours, 168))
    audit(logger, "admin.pipeline_stream", user=admin.id, hours=window)

    async def frames():
        started = asyncio.get_event_loop().time()
        while True:
            # The client going away is the normal ending.
            if await request.is_disconnected():
                return
            if asyncio.get_event_loop().time() - started > STREAM_MAX_SECONDS:
                # Ends the response; the browser reopens and is authorised again.
                return
            try:
                payload = json.dumps(pipeline(db, hours=window))
            except Exception as exc:  # noqa: BLE001 - one bad frame must not kill the stream
                logger.warning("pipeline stream: frame failed, retrying next tick: %s", exc)
                payload = json.dumps({"error": str(exc)})
            # A blank line terminates one server-sent event.
            yield f"data: {payload}\n\n"
            await asyncio.sleep(STREAM_INTERVAL_SECONDS)

    return StreamingResponse(
        frames(), media_type="text/event-stream",
        # Cloudflare and any proxy in between will happily buffer an event stream into uselessness.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


@router.get("/latency")
def latency(db: Database = Depends(get_db), admin: User = Depends(require_admin), *,
            hours: int = 24) -> dict:
    """The drill-down: the spans, then where the time actually went, then when."""
    window = max(1, min(hours, 24 * 7))
    audit(logger, "admin.latency", user=admin.id, hours=window)
    # One row per zone the posting crosses, in pipeline order; the end-to-end span; the lanes; by hour.
    return latency_report(db, hours=window)


@router.get("/latency/{key}")
def latency_stage(key: str, db: Database = Depends(get_db),
                  admin: User = Depends(require_admin), *, hours: int = 24) -> dict:
    """One zone, expanded: waiting versus running, the backlog, and the hour-by-hour shape."""
    window = max(1, min(hours, 24 * 7))
    try:
        detail = stage_detail(db, key, hours=window)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such stage") from None
    audit(logger, "admin.latency_stage", user=admin.id, stage=key, hours=window)
    return detail

