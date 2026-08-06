"""The dashboard, and the feed behind it."""
from __future__ import annotations

import logging
import uuid as _uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from libs import settings
from libs.contracts.config import migrate_config
from libs.corpus import salary
from libs.db.models import CorpusJob, User, UserDocument, UserJobState, UserLabel, UserModel
from libs.onboarding import onboarding_state
from libs.profiles.repository import ProfileFiles
from libs.scoring.history import model_history
from libs.scoring.rules import score_job
from libs.scoring.thresholds import cutoff_for
from libs.tenant.repository import TenantRepository
from services.api.admin_routes import admin_emails
from services.api.deps import current_user, get_db
from services.api.status_routes import daily_cap

logger = logging.getLogger(__name__)
router = APIRouter(tags=["dashboard"])

# Where a posting with no date sorts against one that has one.
_UNDATED = datetime.min.replace(tzinfo=UTC)


def _is_admin(user: User) -> bool:
    """The same rule `require_admin` enforces, in one place."""
    return bool(user.is_admin) or user.email.lower() in admin_emails()


# How many corpus rows the stage-0 feed ranks in Python.
STAGE0_SCAN = 2_000
FEED_PAGE = 25


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db),
              user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Everything the front page shows, in one call."""
    from services.api.process_routes import state as process_state  # noqa: PLC0415 - one caller

    user = db.get(User, user_id)
    onboarding = onboarding_state(db, user_id)
    if user.onboarding_completed_at is None:
        # Nothing below is meaningful yet, and empty panels read as a broken product.
        return {"stage": "onboarding", "onboarding": onboarding.as_dict(),
                "is_admin": _is_admin(user)}

    # Past this point onboarding is finished; the branch above tests the stored timestamp.

    model = db.scalar(
        select(UserModel).where(UserModel.user_id == user_id)
        .order_by(UserModel.version.desc()).limit(1))
    scored = db.scalar(select(func.count()).select_from(UserJobState)
                       .where(UserJobState.user_id == user_id,
                              UserJobState.score.isnot(None))) or 0
    labels = db.scalar(select(func.count()).select_from(UserLabel)
                       .where(UserLabel.user_id == user_id,
                              UserLabel.profile_generation == user.profile_generation)) or 0
    applied = db.scalar(select(func.count()).select_from(UserJobState)
                        .where(UserJobState.user_id == user_id,
                               UserJobState.applied_at.isnot(None))) or 0

    servable = bool(model and model.profile_generation == user.profile_generation)
    return {
        "stage": "personal" if servable else "rules",
        "next": process_state(db, user_id),
        "model": {
            "version": user.model_version,
            "servable": servable,
            # Named rather than implied.
            "stale_reason": None if servable else _stale_reason(user, model),
            "trained_at": model.created_at.isoformat() if model and model.created_at else None,
            # Every version, not just the live one: "is this getting better" is about the sequence.
            "history": model_history(db, user_id),
        },
        # Whether a model can be trained, by the same predicate the trainer refuses on.
        "may_train": not ProfileFiles(db, user_id).missing_setup(),
        "feed": {"scored": scored, "labels": labels, "applied": applied},
        "documents": db.scalar(select(func.count()).select_from(UserDocument)
                               .where(UserDocument.user_id == user_id)) or 0,
        "telegram": user.telegram_status,
        # The cadence is the platform's, so the page must not imply it is theirs to change.
        "notifications": {"per_day_cap": daily_cap(user),
                          "interval_min": int(settings.get(db, "delivery_interval_min"))},
        "banners": _banners(user, servable, onboarding),
        # Whether to show the operator panel at all.
        "is_admin": _is_admin(user),
    }


def _stale_reason(user: User, model: UserModel | None) -> str:
    if model is None:
        return "you have no model yet — your jobs are ranked by your keywords until you train one"
    return ("your profile changed after this model was trained, so your jobs need relabelling and "
            "a retrain")


def _banners(user: User, servable: bool, onboarding) -> list[dict]:
    """The health warnings of `OPERATIONS_PLAN.md` §4b — failures the user can fix, said plainly."""
    banners = []
    if user.telegram_status == "broken":
        banners.append({"level": "error", "key": "telegram_broken",
                        "text": "Your bot can no longer message you — reconnect it in settings."})
    elif user.telegram_status != "connected":
        banners.append({"level": "warning", "key": "telegram_unconfigured",
                        "text": "Telegram is not connected, so nothing is being delivered."})
    if not user.active and servable:
        banners.append({"level": "info", "key": "paused",
                        "text": "Your feed is paused. Turn it on to start receiving jobs."})

    # Anything that was satisfied at signup and is not any more.
    regressed = [page for page in onboarding.pages
                 if not page.complete and page.key != "telegram"]
    for page in regressed:
        banners.append({"level": "warning", "key": f"incomplete:{page.key}",
                        "text": f"{page.title}: {page.detail}"})
    return banners


@router.get("/feed")
def feed(*, db: Session = Depends(get_db), user_id: _uuid.UUID = Depends(current_user),
         limit: int = Query(FEED_PAGE, ge=1, le=100),
         offset: int = Query(0, ge=0),
         q: str | None = Query(None, max_length=200),
         source: str | None = Query(None, max_length=64),
         remote: bool | None = Query(None),
         applied: bool | None = Query(None),
         max_age_days: int | None = Query(None, ge=1, le=365),
         min_score: float | None = Query(None, ge=0, le=100),
         sort: str = Query("score", pattern="^(score|posted|fetched|title|company)$"),
         direction: str = Query("desc", pattern="^(asc|desc)$")) -> dict:
    """The ranked jobs — a page of them, with the filters the table offers."""
    user = db.get(User, user_id)
    model = db.scalar(
        select(UserModel).where(UserModel.user_id == user_id,
                                UserModel.profile_generation == user.profile_generation)
        .limit(1))
    sources = sorted(db.scalars(select(CorpusJob.source).distinct()).all())

    if model is not None:
        floor = cutoff_for(db, user_id) if min_score is None else min_score
        conditions = [UserJobState.user_id == user_id,
                      UserJobState.score.isnot(None),
                      UserJobState.score >= floor,
                      UserJobState.dismissed_at.is_(None)]
        if q:
            pattern = f"%{q}%"
            conditions.append(CorpusJob.title.ilike(pattern)
                              | CorpusJob.company.ilike(pattern)
                              | CorpusJob.url.ilike(pattern))
        if source:
            conditions.append(CorpusJob.source == source)
        if remote is not None:
            conditions.append(CorpusJob.is_remote.is_(remote))
        if applied is not None:
            conditions.append(UserJobState.applied_at.isnot(None) if applied
                              else UserJobState.applied_at.is_(None))
        if max_age_days is not None:
            cutoff = func.now() - func.make_interval(0, 0, 0, max_age_days)
            conditions.append(CorpusJob.posted_at.is_(None) | (CorpusJob.posted_at >= cutoff))

        order_column = {"score": UserJobState.score, "posted": CorpusJob.posted_at,
                        "fetched": CorpusJob.first_seen_at,
                        "title": CorpusJob.title, "company": CorpusJob.company}[sort]
        ordering = order_column.desc().nulls_last() if direction == "desc" \
            else order_column.asc().nulls_last()

        base = (select(CorpusJob, UserJobState)
                .join(UserJobState, UserJobState.job_id == CorpusJob.id)
                .where(*conditions))
        total = db.scalar(select(func.count()).select_from(base.subquery()))
        rows = db.execute(base.order_by(ordering, CorpusJob.id).offset(offset).limit(limit)).all()
        return {"stage": "personal", "approximate": False, "total": total, "sources": sources,
                "jobs": [_item(job, state.score, state.applied_at) for job, state in rows]}

    jobs, total = _rules_feed(db, user_id, limit=limit, offset=offset, q=q, source=source,
                              remote=remote, applied=applied, max_age_days=max_age_days,
                              sort=sort, direction=direction)
    return {"stage": "rules", "approximate": True, "total": total, "sources": sources,
            "detail": ("Ranked by your keywords until your own model has been trained. It will "
                       "get much better."),
            "jobs": jobs}


def _rules_feed(db: Session, user_id: _uuid.UUID, *, limit: int, offset: int,
                q: str | None, source: str | None, remote: bool | None, applied: bool | None,
                max_age_days: int | None, sort: str, direction: str) -> tuple[list[dict], int]:
    """Stage 0."""
    config = TenantRepository(db).get_config(user_id)
    if not config:
        return [], 0
    config = migrate_config(config)

    states = db.execute(
        select(UserJobState.job_id, UserJobState.dismissed_at, UserJobState.applied_at)
        .where(UserJobState.user_id == user_id)).all()
    dismissed = {job_id for job_id, dismissed_at, _ in states if dismissed_at is not None}
    applied_at = {job_id: at for job_id, _, at in states if at is not None}
    rows = db.scalars(
        select(CorpusJob).order_by(CorpusJob.first_seen_at.desc()).limit(STAGE0_SCAN)).all()

    needle = q.lower() if q else None
    cutoff = datetime.now(UTC) - timedelta(days=max_age_days) if max_age_days else None
    scored = []
    for row in rows:
        if row.id in dismissed:
            continue
        if needle and needle not in " ".join(
                part for part in (row.title, row.company, row.url) if part).lower():
            continue
        if source and row.source != source:
            continue
        if remote is not None and bool(row.is_remote) != remote:
            continue
        if applied is not None and (row.id in applied_at) != applied:
            continue
        if cutoff and row.posted_at is not None and row.posted_at < cutoff:
            continue
        result = score_job(config, title=row.title, description=row.description,
                           is_remote=row.is_remote)
        if not result.excluded:
            scored.append((result.score, row))

    if sort == "score":
        # Freshness breaks a tie.
        scored.sort(
            key=lambda item: (item[0], item[1].posted_at or _UNDATED),
            reverse=direction == "desc")
    elif sort in ("posted", "fetched"):
        # Undated rows go last whichever way the dated ones are ordered.
        column = "posted_at" if sort == "posted" else "first_seen_at"
        scored.sort(key=lambda item: (getattr(item[1], column) is None,
                                      (getattr(item[1], column)
                                       or datetime.min.replace(tzinfo=UTC))
                                      .timestamp() * (-1 if direction == "desc" else 1)))
    else:
        scored.sort(key=lambda item: (getattr(item[1], sort) is None,
                                      (getattr(item[1], sort) or "").lower()),
                    reverse=direction == "desc")

    page = scored[offset:offset + limit]
    return [_item(row, score, applied_at.get(row.id)) for score, row in page], len(scored)


def _item(job: CorpusJob, score: float | None, applied_at=None) -> dict:
    """One feed row."""
    return {
        "id": job.id,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "is_remote": job.is_remote,
        "url": job.url,
        "source": job.source,
        "score": round(float(score), 1) if score is not None else None,
        "posted_at": job.posted_at.isoformat() if job.posted_at else None,
        "first_seen_at": job.first_seen_at.isoformat() if job.first_seen_at else None,
        "salary": salary.extract(job.description),
        "applied_at": applied_at.isoformat() if applied_at else None,
    }
