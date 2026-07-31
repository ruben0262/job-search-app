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
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from libs.contracts.config import migrate_config
from libs.corpus import salary
from libs.db.models import CorpusJob, User, UserDocument, UserJobState, UserLabel, UserModel
from libs.onboarding import onboarding_state
from libs.scoring.history import model_history
from libs.scoring.rules import score_job
from libs.scoring.thresholds import cutoff_for
from libs.tenant.repository import TenantRepository
from services.api.admin_routes import admin_emails
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
            # Every version, not just the live one: "is this getting better" is a question about
            # the sequence, and it is the question a user paying for their own training has.
            "history": model_history(db, user_id),
        },
        "feed": {"scored": scored, "labels": labels, "applied": applied},
        "documents": db.scalar(select(func.count()).select_from(UserDocument)
                               .where(UserDocument.user_id == user_id)) or 0,
        "telegram": user.telegram_status,
        "notifications": {"per_day_cap": _cap(db, user_id),
                          "interval_min": user.notify_interval_min},
        "banners": _banners(user, servable, onboarding),
        # **Whether to show the operator panel at all.** The API already refuses `/admin/*` to
        # anyone not in `ADMIN_EMAILS`, so this is a rendering hint and not the access control —
        # but without it the SPA would have to probe an endpoint it expects to be denied, and a
        # 403 on every dashboard load is indistinguishable from a real one in the console.
        "is_admin": user.email.lower() in admin_emails(),
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
    """The ranked jobs — a page of them, with the filters the table offers.

    The stage is in the response because a feed that silently changes how it ranks is one nobody can
    reason about — and a new account's first feed *is* the keyword one, which is worth saying rather
    than passing off as the model's work.

    **The age filter keeps undated rows.** Same rule as delivery: a missing `posted_at` is unknown,
    not old, and a preference filter fails open. `total` and `sources` are for the table's pager and
    source dropdown — computed under the same filters as the page, or the counts would lie.

    **`min_score` defaults to the delivery cutoff, not to zero.** The corpus is shared and the
    harvest is broad, so most of what is scored for any given user is not for them: a page showing
    everything is a page of other people's jobs with a number beside them. The default is the same
    threshold that decides what reaches Telegram, so the table and the channel agree about what
    counts as a match. Passing `min_score=0` explicitly widens it, which is a thing a user may
    genuinely want and should have to ask for.
    """
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
    """Stage 0. Scored in Python over the most recent slice, not in SQL.

    In Python because the rule is a word-boundary match tolerant of the space/hyphen split, and
    expressing that as SQL would either be wrong (`LIKE '%full-stack%'` misses "Full Stack") or a
    regular expression per keyword per row, which is not faster for being in the database. The
    filters follow the scoring for the same reason: one list, filtered once, is simpler than the
    same conditions split across SQL and Python.
    """
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
                           location=row.location, is_remote=row.is_remote)
        if not result.excluded:
            scored.append((result.score, row))

    if sort == "score":
        scored.sort(key=lambda item: item[0], reverse=direction == "desc")
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
    """One feed row. **No description** — it is up to 2,500 characters per job and the list does not
    show it; fetching 25 of them would be most of the response for none of the value. The salary is
    the one thing the description holds that the table wants, so it is extracted here (regex, free)
    rather than shipped raw."""
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
