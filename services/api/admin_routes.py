"""The operator's read-only view of the platform."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid as _uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from libs.db.models import (
    AtsToken,
    CorpusJob,
    OperatorKeyword,
    QueueJob,
    User,
    UserConfig,
    UserJobState,
)
from libs.harvest import distinct_keywords
from libs.limits import all_for, describe, set_limit
from libs.obs import audit
from libs.ops import evaluate, platform_health, render
from libs.ops.latency import (
    filter_effect,
    lane_timings,
    latency_series,
    pipeline_latency,
    stage_breakdown,
    stage_detail,
)
from libs.ops.pipeline import pipeline
from libs.profiles.repository import ProfileFiles
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
    source_min_ages,
)
from libs.tenant.repository import TenantRepository
from services.api.deps import current_user, get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])


def admin_emails() -> set[str]:
    """Read per call rather than cached at import: a restart is a slow way to fix an env var."""
    raw = os.environ.get("ADMIN_EMAILS", "")
    return {entry.strip().lower() for entry in raw.split(",") if entry.strip()}


def require_admin(db: Session = Depends(get_db),
                  user_id: _uuid.UUID = Depends(current_user)) -> User:
    """404, not 403."""
    user = db.get(User, user_id)
    allowed = admin_emails()
    # The env var or the row.
    granted = user is not None and (user.is_admin or user.email.lower() in allowed)
    if not granted:
        logger.warning("admin: refused %s", user_id)
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")
    return user


@router.get("/health")
def health(db: Session = Depends(get_db), admin: User = Depends(require_admin)) -> dict:
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


@router.get("/users")
def users(db: Session = Depends(get_db), admin: User = Depends(require_admin)) -> dict:
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
                "plan": row.plan_id,
                "telegram": row.telegram_status,
                "profile_generation": row.profile_generation,
                "model_version": row.model_version,
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
            for row in db.scalars(select(User).order_by(User.created_at))],
    }


class AdminAccount(BaseModel):
    id: str
    email: str
    status: str
    active: bool
    plan: str
    telegram: str
    profile_generation: int
    profile_files_visible: bool
    onboarded: bool
    created_at: datetime | None
    last_active_at: datetime | None


class AdminProfileFile(BaseModel):
    name: str
    kind: str
    filled: bool


class AdminSetup(BaseModel):
    missing: list[str]
    files: list[AdminProfileFile]


class AdminCounts(BaseModel):
    applied: int
    notified: int


class AdminAccountDetail(BaseModel):
    user: AdminAccount
    setup: AdminSetup
    # `None` is unlimited and is a real value, not a missing one.
    limits: dict[str, int | None]
    counts: AdminCounts


@router.get("/users/{user_id}")
def user_detail(user_id: _uuid.UUID, db: Session = Depends(get_db),
                admin: User = Depends(require_admin)) -> AdminAccountDetail:
    """One account, for the two questions an operator actually has."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such user")

    files = ProfileFiles(db, user_id)
    audit(logger, "admin.user_detail", user=admin.id, subject=user_id)
    return AdminAccountDetail(
        user=AdminAccount(
            id=str(user.id), email=user.email, status=user.status, active=user.active,
            plan=user.plan_id, telegram=user.telegram_status,
            profile_generation=user.profile_generation,
            profile_files_visible=bool(user.profile_files_visible),
            onboarded=user.onboarding_completed_at is not None,
            created_at=user.created_at, last_active_at=user.last_active_at),
        setup=AdminSetup(
            missing=files.missing_setup(),
            files=[AdminProfileFile(name=row["name"], kind=row["kind"], filled=row["filled"])
                   for row in files.listing()]),
        limits=all_for(user),
        counts=AdminCounts(
            applied=db.scalar(select(func.count()).select_from(UserJobState)
                              .where(UserJobState.user_id == user_id,
                                     UserJobState.applied_at.isnot(None))) or 0,
            notified=db.scalar(select(func.count()).select_from(UserJobState)
                               .where(UserJobState.user_id == user_id,
                                      UserJobState.notified_at.isnot(None))) or 0))


class SettingPatch(BaseModel):
    value: dict | list | int | str


@router.get("/settings")
def read_settings(db: Session = Depends(get_db), admin: User = Depends(require_admin)) -> dict:
    """What the operator controls, with the defaults filled in for anything unset."""
    audit(logger, "admin.settings_read", user=admin.id)
    return {"settings": all_settings(db), "defaults": DEFAULTS}


@router.put("/settings/{key}")
def write_setting(key: str, body: SettingPatch, db: Session = Depends(get_db),
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


class FilesFlag(BaseModel):
    visible: bool


@router.put("/users/{user_id}/files")
def set_files_visible(user_id: _uuid.UUID, body: FilesFlag, db: Session = Depends(get_db),
                      admin: User = Depends(require_admin)) -> dict:
    """Open or close this account's profile files to the person they are about."""
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such account")
    target.profile_files_visible = body.visible
    audit(logger, "admin.files_visibility_changed", user=admin.id, subject=user_id,
          visible=body.visible)
    return {"status": "saved", "profile_files_visible": body.visible}


@router.put("/users/{user_id}/admin")
def set_admin(user_id: _uuid.UUID, body: AdminFlag, db: Session = Depends(get_db),
              admin: User = Depends(require_admin)) -> dict:
    """Grant or revoke operator access."""
    if user_id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="You cannot change your own operator access.")

    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such user")

    target.is_admin = body.is_admin
    audit(logger, "admin.access_changed", user=admin.id, subject=user_id, granted=body.is_admin)
    return {"status": "ok", "id": str(user_id), "is_admin": target.is_admin}


class DeleteConfirmation(BaseModel):
    # The typed address, which must match the account being deleted.
    email: str


@router.delete("/users/{user_id}")
def delete_user(user_id: _uuid.UUID, body: DeleteConfirmation, db: Session = Depends(get_db),
                admin: User = Depends(require_admin)) -> dict:
    """Delete an account and everything belonging to it, immediately and permanently."""
    if user_id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="You cannot delete your own account from here.")

    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such user")

    if body.email.strip().lower() != target.email.lower():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="That address does not match the account. Nothing was deleted.")

    email = target.email
    db.delete(target)
    audit(logger, "admin.user_deleted", user=admin.id, subject=user_id)
    logger.warning("admin: %s deleted account %s (%s)", admin.id, user_id, email)
    return {"status": "deleted", "id": str(user_id)}


@router.get("/sources")
def sources(db: Session = Depends(get_db), admin: User = Depends(require_admin)) -> dict:
    """Every source the harvester collects from, with the freshness window each one is using."""
    # One number per query-taking source: a source is asked once per distinct keyword.
    wanted = len(distinct_keywords(TenantRepository(db).active_configs()))
    asked = QUERY_TAKING_SOURCES | {"linkedin"}
    floors = source_min_ages(db)
    return {
        "sources": [{"name": name,
                     "hours": freshness_hours(db, name),
                     # Its twin, shown beside it: `hours` is how far back the source is asked,
                     # this is how long what it returned stays deliverable. They were set in two
                     # different places, and only this one had no control at all — so widening a
                     # source's harvest window did nothing an operator could see if the floor
                     # below was discarding the result.
                     "min_age_hours": floors.get(name, DEFAULT_SOURCE_MIN_AGE_HOURS),
                     "queries_per_cycle": queries_per_cycle(db, name),
                     # `None` where the source has no result cap to set at all.
                     "results_per_query": results_per_query(db, name),
                     # `None` for a source that fetches one response rather than walking pages.
                     "pages_per_cycle": pages_per_cycle(db, name),
                     "wanted": wanted if name in asked else 0}
                    for name in HARVEST_SOURCES],
        # The ATS ceiling, with the number it is a ceiling on.
        "ats": {
            "companies_per_cycle": ats_companies_per_cycle(db),
            "companies_held": db.scalar(
                select(func.count()).select_from(AtsToken)) or 0,
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
                   db: Session = Depends(get_db),
                   admin: User = Depends(require_admin)) -> dict:
    """Set one ceiling on one account, or clear it to unlimited."""
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such user")

    try:
        set_limit(db, user_id, key, body.value)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    audit(logger, "admin.limit_set", user=admin.id, subject=user_id, limit=key, value=body.value)
    return {"status": "ok", "limits": all_for(target)}

# What an operator can page through at once.
CORPUS_PAGE = 50


@router.get("/corpus")
def corpus(db: Session = Depends(get_db), admin: User = Depends(require_admin), *,
           q: str = "", source: str = "", days: int = 30,
           offset: int = 0) -> dict:
    """Search the shared corpus."""
    window = datetime.now(UTC) - timedelta(days=max(days, 1))
    filters = [CorpusJob.first_seen_at >= window]
    if q.strip():
        needle = f"%{q.strip()}%"
        filters.append(or_(CorpusJob.title.ilike(needle), CorpusJob.company.ilike(needle)))
    if source.strip():
        filters.append(CorpusJob.source == source.strip())

    total = db.scalar(select(func.count()).select_from(CorpusJob).where(*filters)) or 0
    rows = db.scalars(
        select(CorpusJob).where(*filters)
        .order_by(CorpusJob.first_seen_at.desc())
        .offset(max(offset, 0)).limit(CORPUS_PAGE)).all()

    by_source = db.execute(
        select(CorpusJob.source, func.count())
        .where(CorpusJob.first_seen_at >= window)
        .group_by(CorpusJob.source).order_by(func.count().desc())).all()

    audit(logger, "admin.corpus", user=admin.id, q=q[:60], total=total)
    return {
        "total": total,
        "offset": max(offset, 0),
        "page_size": CORPUS_PAGE,
        "sources": [{"source": name or "unknown", "count": count} for name, count in by_source],
        "jobs": [
            {"id": row.id, "title": row.title, "company": row.company,
             "location": row.location, "source": row.source, "is_remote": row.is_remote,
             # Whether it can be scored at all.
             "embedded": row.embedding is not None,
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
def keywords(db: Session = Depends(get_db), admin: User = Depends(require_admin)) -> dict:
    """Every term the harvest currently searches, and who it belongs to."""
    injected = db.scalars(select(OperatorKeyword).order_by(OperatorKeyword.keyword)).all()

    owned: dict[str, set[str]] = {}
    rows = db.execute(select(UserConfig.user_id, UserConfig.config)).all()
    emails = dict(db.execute(select(User.id, User.email)).all())
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
def add_keyword(body: KeywordBody, db: Session = Depends(get_db),
                admin: User = Depends(require_admin)) -> dict:
    """Inject a term into the harvest, to fill the corpus ahead of the users who will need it."""
    term = " ".join(body.keyword.split()).lower()
    place = (body.location or "").strip() or None
    existing = db.scalar(
        select(OperatorKeyword).where(OperatorKeyword.keyword == term,
                                      OperatorKeyword.location == place))
    if existing is not None:
        # Idempotent: adding the same term twice is a person being unsure, not a request for two.
        return {"id": existing.id, "keyword": existing.keyword, "location": existing.location,
                "created": False}

    row = OperatorKeyword(keyword=term, location=place, note=(body.note or "").strip() or None,
                          created_by=admin.email)
    db.add(row)
    db.flush()
    audit(logger, "admin.keyword_added", user=admin.id, keyword=term, location=place)
    return {"id": row.id, "keyword": row.keyword, "location": row.location, "created": True}


@router.delete("/keywords/{keyword_id}")
def remove_keyword(keyword_id: int, db: Session = Depends(get_db),
                   admin: User = Depends(require_admin)) -> dict:
    """Remove an injected term."""
    row = db.get(OperatorKeyword, keyword_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such keyword")
    db.delete(row)
    audit(logger, "admin.keyword_removed", user=admin.id, keyword=row.keyword)
    return {"status": "removed", "keyword": row.keyword}

@router.delete("/queue/dead")
def discard_dead_letters(db: Session = Depends(get_db), admin: User = Depends(require_admin), *,
                         lane: str | None = None) -> dict:
    """Clear dead-lettered jobs an operator has read and dealt with."""
    query = select(QueueJob).where(QueueJob.state == "dead")
    if lane:
        query = query.where(QueueJob.lane == lane)

    rows = list(db.scalars(query))
    for row in rows:
        logger.warning("admin: discarding dead %s job %s for user %s — last error: %s",
                       row.lane, row.id, row.user_id, (row.last_error or "")[:200])
        db.delete(row)

    audit(logger, "admin.dead_discarded", user=admin.id, lane=lane or "all", count=len(rows))
    return {"status": "discarded", "count": len(rows), "lane": lane}


@router.get("/pipeline")
def pipeline_shape(db: Session = Depends(get_db), admin: User = Depends(require_admin), *,
                   hours: int = 24) -> dict:
    """Where the jobs are right now, stage by stage."""
    return pipeline(db, hours=max(1, min(hours, 168)))


# How often the live view recomputes.
STREAM_INTERVAL_SECONDS = 1.0

# A stream is an authorisation held open.
STREAM_MAX_SECONDS = 300


@router.get("/pipeline/stream")
async def pipeline_stream(request: Request, db: Session = Depends(get_db),
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
                # Each frame starts clean, or Postgres answers every one from the first frame's snapshot.
                db.rollback()
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
def latency(db: Session = Depends(get_db), admin: User = Depends(require_admin), *,
            hours: int = 24) -> dict:
    """The drill-down: the spans, then where the time actually went, then when."""
    window = max(1, min(hours, 24 * 7))
    audit(logger, "admin.latency", user=admin.id, hours=window)
    return {
        "window_hours": window,
        # One row per zone the posting crosses, in pipeline order.
        "stages": stage_breakdown(db, hours=window),
        # What the pre-score filter removed, per account.
        "filter": filter_effect(db, hours=window),
        "spans": pipeline_latency(db, hours=window),
        "lanes": lane_timings(db, hours=window),
        "series": latency_series(db, hours=window),
    }


@router.get("/latency/{key}")
def latency_stage(key: str, db: Session = Depends(get_db),
                  admin: User = Depends(require_admin), *, hours: int = 24) -> dict:
    """One zone, expanded: waiting versus running, the backlog, and the hour-by-hour shape."""
    window = max(1, min(hours, 24 * 7))
    try:
        detail = stage_detail(db, key, hours=window)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such stage") from None
    audit(logger, "admin.latency_stage", user=admin.id, stage=key, hours=window)
    return detail

