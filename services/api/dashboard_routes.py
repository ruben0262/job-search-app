"""The dashboard, and the feed behind it.

**State-driven, not a wall of buttons** (`ONBOARDING_AND_PROCESSING.md` §4). One endpoint reads the
counters and says what the account needs *now*; it does not offer four actions and hope they are
pressed in the right order. That matters because the order is not guessable — processing invalidates
labels, relabelling costs real money, and training is free — and a user who presses them in the
wrong order pays for the mistake.

**The feed is stage-aware.** Before the first model exists it is ranked by the config alone
(`libs.scoring.rules`, stage 0), and it says so. A feed that silently drops from "your model" to
"keyword matching" and back is one nobody can reason about, so the stage is part of the response
rather than an implementation detail.
"""
from __future__ import annotations

import logging
import uuid as _uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from libs.contracts.config import migrate_config
from libs.db.models import CorpusJob, User, UserDocument, UserJobState, UserLabel, UserModel
from libs.onboarding import onboarding_state
from libs.scoring.rules import score_job
from libs.tenant.repository import TenantRepository
from services.api.deps import current_user, get_db

logger = logging.getLogger(__name__)
router = APIRouter(tags=["dashboard"])

# How many corpus rows the stage-0 feed ranks in Python. Bounded because this runs in a request and
# the corpus keeps growing; a new account's feed does not get better for scanning ten thousand rows,
# and the personal model takes over within the hour anyway.
STAGE0_SCAN = 2_000
FEED_PAGE = 25


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db),
              user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Everything the front page shows, in one call.

    One call rather than five, because the panels are not independent: "you have 3,310 labels" and
    "your model is stale" contradict each other if they are read a few seconds apart while a worker
    is running.
    """
    from services.api.process_routes import state as process_state  # noqa: PLC0415 - one caller

    user = db.get(User, user_id)
    onboarding = onboarding_state(db, user_id)
    if user.onboarding_completed_at is None:
        # Nothing below is meaningful yet, and showing empty panels reads as a broken product
        # rather than an unfinished signup.
        return {"stage": "onboarding", "onboarding": onboarding.as_dict()}

    # Past this point the account **has** finished onboarding, and the branch above deliberately
    # tests the stored timestamp rather than live completeness. They differ: revoking a credential
    # later makes a page incomplete again, and sending a working account back to the signup wizard
    # for a broken bot token would hide the dashboard that explains what broke. Regressions surface
    # as banners instead — visible, actionable, and not a wall.

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
            # Named rather than implied. "Your model was trained before your last profile change"
            # is a sentence a user can act on; a greyed-out button is not.
            "stale_reason": None if servable else _stale_reason(user, model),
            "trained_at": model.created_at.isoformat() if model and model.created_at else None,
        },
        "feed": {"scored": scored, "labels": labels, "applied": applied},
        "documents": db.scalar(select(func.count()).select_from(UserDocument)
                               .where(UserDocument.user_id == user_id)) or 0,
        "telegram": user.telegram_status,
        "notifications": {"per_day_cap": _cap(db, user_id),
                          "interval_min": user.notify_interval_min},
        "banners": _banners(user, servable, onboarding),
    }


def _stale_reason(user: User, model: UserModel | None) -> str:
    if model is None:
        return "you have no model yet — your jobs are ranked by your keywords until you train one"
    return ("your profile changed after this model was trained, so your jobs need relabelling and "
            "a retrain")


def _cap(db: Session, user_id: _uuid.UUID) -> int:
    config = TenantRepository(db).get_config(user_id) or {}
    return migrate_config(config).get("max_notifications_per_day", 20)


def _banners(user: User, servable: bool, onboarding) -> list[dict]:
    """The health warnings of `OPERATIONS_PLAN.md` §4b — failures the user can fix, said plainly.

    Each one is a thing that silently stops the product working. A broken Telegram token means the
    feed still runs and nothing is ever delivered, which looks like "no jobs matched" from where the
    user is sitting.
    """
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

    # Anything that was satisfied at signup and is not any more. Telegram has its own banner above
    # because its failure mode is specific; the rest are reported generically rather than not at
    # all, since a missing Claude credential or a deleted config stops the product just as dead.
    regressed = [page for page in onboarding.pages
                 if not page.complete and page.key != "telegram"]
    for page in regressed:
        banners.append({"level": "warning", "key": f"incomplete:{page.key}",
                        "text": f"{page.title}: {page.detail}"})
    return banners


@router.get("/feed")
def feed(db: Session = Depends(get_db), user_id: _uuid.UUID = Depends(current_user),
         limit: int = Query(FEED_PAGE, ge=1, le=100)) -> dict:
    """The ranked jobs. Personal model if there is a servable one, otherwise the config alone.

    The stage is in the response because a feed that silently changes how it ranks is one nobody can
    reason about — and a new account's first feed *is* the keyword one, which is worth saying rather
    than passing off as the model's work.
    """
    user = db.get(User, user_id)
    model = db.scalar(
        select(UserModel).where(UserModel.user_id == user_id,
                                UserModel.profile_generation == user.profile_generation)
        .limit(1))

    if model is not None:
        rows = db.execute(
            select(CorpusJob, UserJobState.score)
            .join(UserJobState, UserJobState.job_id == CorpusJob.id)
            .where(UserJobState.user_id == user_id,
                   UserJobState.score.isnot(None),
                   UserJobState.dismissed_at.is_(None))
            .order_by(UserJobState.score.desc())
            .limit(limit)).all()
        return {"stage": "personal", "approximate": False,
                "jobs": [_item(job, score) for job, score in rows]}

    return {"stage": "rules", "approximate": True,
            "detail": ("Ranked by your keywords until your own model has been trained. It will "
                       "get much better."),
            "jobs": _rules_feed(db, user_id, limit)}


def _rules_feed(db: Session, user_id: _uuid.UUID, limit: int) -> list[dict]:
    """Stage 0. Scored in Python over the most recent slice, not in SQL.

    In Python because the rule is a word-boundary match tolerant of the space/hyphen split, and
    expressing that as SQL would either be wrong (`LIKE '%full-stack%'` misses "Full Stack") or a
    regular expression per keyword per row, which is not faster for being in the database.
    """
    config = TenantRepository(db).get_config(user_id)
    if not config:
        return []
    config = migrate_config(config)

    dismissed = set(db.scalars(
        select(UserJobState.job_id).where(UserJobState.user_id == user_id,
                                          UserJobState.dismissed_at.isnot(None))).all())
    rows = db.scalars(
        select(CorpusJob).order_by(CorpusJob.first_seen_at.desc()).limit(STAGE0_SCAN)).all()

    scored = []
    for row in rows:
        if row.id in dismissed:
            continue
        result = score_job(config, title=row.title, description=row.description,
                           location=row.location, is_remote=row.is_remote)
        if not result.excluded:
            scored.append((result.score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [_item(row, score) for score, row in scored[:limit]]


def _item(job: CorpusJob, score: float | None) -> dict:
    """One feed row. **No description** — it is up to 2,500 characters per job and the list does not
    show it; fetching 25 of them would be most of the response for none of the value."""
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
    }
