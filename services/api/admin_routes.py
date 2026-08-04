"""The operator's read-only view of the platform.

**Read-only, deliberately** (`OPERATIONS_PLAN.md` §5 describes an action-capable console; this is
the first half of it). Every action worth having — suspending an account, resetting a credential,
re-running a job — is one an operator can do from the box today, and each needs its own audit
trail. Shipping the reading half now is what makes the platform's failures visible before the first
invited user; shipping half of the acting half would be worse than none.

**Who counts as an operator is an environment variable, not a database flag.** `ADMIN_EMAILS` is
read once per request from the process environment, so granting admin means a deploy — which is
exactly the friction it should have. A boolean column would be one SQL UPDATE away from a privilege
escalation, and the audit trail for that update would live in the same database it grants access to.
"""
from __future__ import annotations

import logging
import os
import uuid as _uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from libs.db.models import (
    CorpusJob,
    OperatorKeyword,
    User,
    UserConfig,
    UserJobState,
    UserLabel,
)
from libs.harvest import distinct_keywords
from libs.limits import all_for, describe, set_limit
from libs.obs import audit
from libs.ops import evaluate, platform_health, render
from libs.ops.latency import (
    lane_timings,
    latency_series,
    pipeline_latency,
    stage_breakdown,
    stage_detail,
)
from libs.profiles.repository import ProfileFiles
from libs.scoring.history import model_history
from libs.settings import (
    DEFAULTS,
    HARVEST_SOURCES,
    QUERY_TAKING_SOURCES,
    all_settings,
    freshness_hours,
    put,
    queries_per_cycle,
)
from libs.tenant.repository import TenantRepository
from services.api.deps import current_user, get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])


def admin_emails() -> set[str]:
    """Read per call rather than cached at import: a container restart is a slow way to fix a typo
    in an env var, and this is not a hot path."""
    raw = os.environ.get("ADMIN_EMAILS", "")
    return {entry.strip().lower() for entry in raw.split(",") if entry.strip()}


def require_admin(db: Session = Depends(get_db),
                  user_id: _uuid.UUID = Depends(current_user)) -> User:
    """404, not 403.

    A 403 confirms the endpoint exists and that somebody is worth phishing for access to it. There
    is no legitimate client that discovers these routes by probing, so there is nothing to lose by
    denying they are there.
    """
    user = db.get(User, user_id)
    allowed = admin_emails()
    # **The env var or the row.** `ADMIN_EMAILS` remains the bootstrap and cannot be revoked from a
    # browser; the flag is how access is granted without a deploy.
    granted = user is not None and (user.is_admin or user.email.lower() in allowed)
    if not granted:
        logger.warning("admin: refused %s", user_id)
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")
    return user


@router.get("/health")
def health(db: Session = Depends(get_db), admin: User = Depends(require_admin)) -> dict:
    """Every number, plus the conditions that currently need a person.

    The alerts are computed here rather than left to the reader, because a page of numbers is
    something an operator scans and an alert list is something they act on — and the whole argument
    of `OPERATIONS_PLAN.md` §1 is that a failure nobody is told about is a failure nobody fixes.
    """
    state = platform_health(db)
    alerts = evaluate(state)
    audit(logger, "admin.health", user=admin.id, alerts=len(alerts))
    return {
        **state.as_dict(),
        # **How long a posting takes to reach somebody.** The product's claim is timeliness and
        # nothing measured it: a four-hour freshness window means little if the pipeline itself
        # spends two of them.
        "latency": pipeline_latency(db),
        "alerts": [
            {"severity": alert.severity, "key": alert.key, "text": alert.text,
             "action": alert.action, "user_email": alert.user_email}
            for alert in alerts],
        "summary": render(alerts),
    }


@router.get("/users")
def users(db: Session = Depends(get_db), admin: User = Depends(require_admin)) -> dict:
    """The account list, with **no credential material of any kind**.

    Not even `last_4`. An operator does not need it to answer any question they have, and a support
    view that shows a fragment of a key is a support view that leaks one over somebody's shoulder.
    """
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
                # **Effective access, not just the column.** An address in `ADMIN_EMAILS` is an
                # operator whether or not anybody ticked the box, and showing the box unticked for
                # somebody who plainly has access is the panel contradicting itself.
                "is_admin": row.is_admin or row.email.lower() in allowed,
                # Whether the flag can be changed here at all. The env var cannot be revoked from a
                # browser, so the control is shown but locked rather than silently ineffective.
                "admin_locked": row.email.lower() in allowed,
                "onboarded": row.onboarding_completed_at is not None,
                "processed_at": row.processed_at.isoformat() if row.processed_at else None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in db.scalars(select(User).order_by(User.created_at))],
    }


@router.get("/users/{user_id}")
def user_detail(user_id: _uuid.UUID, db: Session = Depends(get_db),
                admin: User = Depends(require_admin)) -> dict:
    """One account, for the two questions an operator actually has.

    **"Why is this person's feed bad" and "did their setup finish"** — both answerable here, and
    neither answerable from the list. The model history is the same builder the user's own Model
    page reads, so an operator and the person they are helping are looking at the same numbers.

    **Read-only, and no credential material of any kind** — not even `last_4`, on the same
    reasoning as the list: an operator does not need it to answer either question, and a support
    view that shows a fragment of a key is one that leaks it over somebody's shoulder. An operator
    who could *edit* a user's model or profile from here is a different product with a different
    consent story.
    """
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such user")

    files = ProfileFiles(db, user_id)
    audit(logger, "admin.user_detail", user=admin.id, subject=user_id)
    return {
        "user": {
            "id": str(user.id),
            "email": user.email,
            "status": user.status,
            "active": user.active,
            "plan": user.plan_id,
            "telegram": user.telegram_status,
            "profile_generation": user.profile_generation,
            "model_version": user.model_version,
            "profile_files_visible": bool(user.profile_files_visible),
            "onboarded": user.onboarding_completed_at is not None,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "last_active_at": user.last_active_at.isoformat() if user.last_active_at else None,
        },
        # Setup completeness, by the same predicate the trainer refuses on — so "their setup looks
        # finished but training is refused" cannot happen without this page showing why.
        "setup": {"missing": files.missing_setup(),
                  "files": [{"name": row["name"], "kind": row["kind"], "filled": row["filled"]}
                            for row in files.listing()]},
        "limits": all_for(user),
        "models": model_history(db, user_id),
        "counts": {
            "labels": db.scalar(select(func.count()).select_from(UserLabel)
                                .where(UserLabel.user_id == user_id)) or 0,
            "scored": db.scalar(select(func.count()).select_from(UserJobState)
                                .where(UserJobState.user_id == user_id,
                                       UserJobState.score.isnot(None))) or 0,
            "applied": db.scalar(select(func.count()).select_from(UserJobState)
                                 .where(UserJobState.user_id == user_id,
                                        UserJobState.applied_at.isnot(None))) or 0,
            "notified": db.scalar(select(func.count()).select_from(UserJobState)
                                  .where(UserJobState.user_id == user_id,
                                         UserJobState.notified_at.isnot(None))) or 0,
        },
    }


class SettingPatch(BaseModel):
    value: dict | list | int | str


@router.get("/settings")
def read_settings(db: Session = Depends(get_db), admin: User = Depends(require_admin)) -> dict:
    """What the operator controls, with the defaults filled in for anything unset.

    **These are properties of the product, not preferences.** Freshness and delivery cadence used to
    be per user, which meant every source had to be fetched at the widest window anybody asked for
    and filtered back down per person — and a user choosing a four-hour cadence was choosing to hear
    about a two-hour-old job. One number per source, one cadence, set here.
    """
    audit(logger, "admin.settings_read", user=admin.id)
    return {"settings": all_settings(db), "defaults": DEFAULTS}


@router.put("/settings/{key}")
def write_setting(key: str, body: SettingPatch, db: Session = Depends(get_db),
                  admin: User = Depends(require_admin)) -> dict:
    """Set one. Unknown keys are refused rather than stored — a typo that persists is a setting
    that silently never applies, which is the failure the key list exists to prevent."""
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
    """Open or close this account's profile files to the person they are about.

    Per account rather than platform-wide: the judgement is about what a particular set of files
    currently says, so one that has been read through can be opened without opening everyone's.
    Closed until somebody decides otherwise -- these are written by an agent and reviewed by nobody,
    and a half-settled sentence about a person is worse read than unread.
    """
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
    """Grant or revoke operator access.

    **An operator cannot revoke their own.** Not paternalism — `ADMIN_EMAILS` is the only route back
    in, and an operator whose address is not in it could lock themselves out of their own platform
    with one click, with a redeploy as the only fix. Granting to yourself is meaningless (you
    already have it) and revoking is the dangerous direction, so the guard covers the whole action.
    """
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
    # The typed address, which must match the account being deleted. A dialog somebody can dismiss
    # by pressing return is not a confirmation.
    email: str


@router.delete("/users/{user_id}")
def delete_user(user_id: _uuid.UUID, body: DeleteConfirmation, db: Session = Depends(get_db),
                admin: User = Depends(require_admin)) -> dict:
    """Delete an account and everything belonging to it, immediately and permanently.

    **The typed address is checked on the server, not only in the browser.** A confirmation enforced
    only in the UI is a confirmation that a mis-aimed script skips entirely, and this endpoint
    destroys a person's profile, labels, documents and model with no undo.

    Deletion cascades from the row: every per-user table declares `ondelete="CASCADE"`, so this is
    one statement rather than a list of tables somebody has to remember to extend. The shared corpus
    is untouched — those rows belong to the platform, not to any account.

    An operator cannot delete themselves. The account performing the deletion is the one that would
    have to clean up afterwards.
    """
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
    """Every source the harvester collects from, with the freshness window each one is using.

    Read from the harvester's own enabled list rather than from whatever happens to be in the
    settings blob: a source nobody has tuned should still appear, with the default it is actually
    running at, or the panel silently omits the boards most likely to need attention.

    **`wanted` is what makes the limit meaningful.** A ceiling of 40 tells an operator nothing on
    its own; 40 against 28 queries waiting says the source is fully served, and 40 against 300 says
    it is four cycles behind. The number is how many distinct queries that source currently has —
    the same set the cycle budgets — so the two columns are the same arithmetic the harvest does.
    """
    # One number for every query-taking source: location left the query axis, so a source is asked
    # once per distinct keyword. Feed-shaped and ATS sources take no query at all and report zero.
    wanted = len(distinct_keywords(TenantRepository(db).active_configs()))
    asked = QUERY_TAKING_SOURCES | {"linkedin"}
    return {"sources": [{"name": name,
                         "hours": freshness_hours(db, name),
                         "queries_per_cycle": queries_per_cycle(db, name),
                         "wanted": wanted if name in asked else 0}
                        for name in HARVEST_SOURCES]}


class LimitPatch(BaseModel):
    # `None` is unlimited and is a real, storable answer — not a missing field. The catalogue says
    # which keys accept it.
    value: int | None = None


@router.get("/limits")
def limit_catalogue(admin: User = Depends(require_admin)) -> dict:
    """What can be set, what it defaults to, and what each one means.

    Served rather than hardcoded in the panel so adding a ceiling is one entry in `libs.limits`
    rather than an entry plus a form field plus a label somebody has to keep in step.
    """
    return {"limits": describe()}


@router.put("/users/{user_id}/limits/{key}")
def set_user_limit(user_id: _uuid.UUID, key: str, body: LimitPatch,
                   db: Session = Depends(get_db),
                   admin: User = Depends(require_admin)) -> dict:
    """Set one ceiling on one account, or clear it to unlimited.

    **The operator owns these, including their own.** A user who could raise their own delivery
    ceiling could get their own Telegram bot rate-limited, and the rest are spend. There is no
    self-protection here as there is for operator access and deletion: raising your own limits is
    not a way to lock yourself out, and an operator is exactly who should be able to.
    """
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

# What an operator can page through at once. The corpus is tens of thousands of rows and this runs
# in a request; a browser that can return all of it is a browser that can time out.
CORPUS_PAGE = 50


@router.get("/corpus")
def corpus(db: Session = Depends(get_db), admin: User = Depends(require_admin), *,
           q: str = "", source: str = "", days: int = 30,
           offset: int = 0) -> dict:
    """Search the shared corpus. **No scores** -- those are per user and this is the shared table.

    The only way to see what the corpus actually holds was SQL, which meant nobody looked. That
    matters more than it sounds: a new account's training data is whatever this table already has
    for their field, and the second account on this platform found 240 usable rows out of 14,359
    because the corpus had accumulated for weeks against one search and hours against theirs.

    Facets rather than a bare list: "what is in here" is answered by source and recency, and the
    counts are the answer an operator is actually looking for.
    """
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
             # Whether it can be scored at all. An unembedded row is invisible to every user, and
             # a corpus full of them looks healthy from a row count alone.
             "embedded": row.embedding is not None,
             "described": bool(row.description and len(row.description) > 400),
             "url": row.url,
             "first_seen_at": row.first_seen_at.isoformat() if row.first_seen_at else None,
             "posted_at": row.posted_at.isoformat() if row.posted_at else None}
            for row in rows],
    }


class KeywordBody(BaseModel):
    keyword: str = Field(min_length=2, max_length=100)
    # Absent means every location the platform already searches, which is what seeding a field
    # rather than a place wants.
    location: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=200)


@router.get("/keywords")
def keywords(db: Session = Depends(get_db), admin: User = Depends(require_admin)) -> dict:
    """Every term the harvest currently searches, and who it belongs to.

    **Users' terms are listed and are not deletable here.** They are what somebody's feed is built
    from; an operator seeing them is support, an operator able to remove them is an outage that
    looks like a setting.
    """
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
        # Read-only, and said so in the payload rather than only in the page, so a client that
        # forgets cannot offer a delete that the server would refuse anyway.
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
        # Idempotent rather than a duplicate query stem: adding the same term twice is a person
        # being unsure whether it took, not a request for two of them.
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
    """Remove an injected term.

    **Only reaches `operator_keywords`.** A user's own search terms live on their config and are not
    addressable from here at all, which is why this takes a row id from that table rather than a
    string that could match either.
    """
    row = db.get(OperatorKeyword, keyword_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such keyword")
    db.delete(row)
    audit(logger, "admin.keyword_removed", user=admin.id, keyword=row.keyword)
    return {"status": "removed", "keyword": row.keyword}

@router.get("/latency")
def latency(db: Session = Depends(get_db), admin: User = Depends(require_admin), *,
            hours: int = 24) -> dict:
    """The drill-down: the spans, then where the time actually went, then when.

    The health page reports that a posting took 36 minutes end to end. That is the number worth
    publishing and useless for fixing anything -- it does not say whether a worker was busy or the
    work was slow, and those are repaired in completely different places. Nor does an average over a
    day show the hour it was four times worse, which is the hour worth looking at.

    Bounded window: a fortnight of hourly buckets is a chart nobody can read and a query nobody
    should run on a page load.
    """
    window = max(1, min(hours, 24 * 7))
    audit(logger, "admin.latency", user=admin.id, hours=window)
    return {
        "window_hours": window,
        # One row per zone the posting crosses, in pipeline order. This is what the table renders;
        # everything below it predates the breakdown and is kept because the health summary and the
        # end-to-end chart still read it.
        "stages": stage_breakdown(db, hours=window),
        "spans": pipeline_latency(db, hours=window),
        "lanes": lane_timings(db, hours=window),
        "series": latency_series(db, hours=window),
    }


@router.get("/latency/{key}")
def latency_stage(key: str, db: Session = Depends(get_db),
                  admin: User = Depends(require_admin), *, hours: int = 24) -> dict:
    """One zone, expanded: waiting versus running, the backlog, and the hour-by-hour shape.

    **Fetched on expansion rather than with the table.** Each of these costs a query and only one
    row is ever open, so putting them on the page load would run six times the work to answer a
    question nobody asked yet.

    `key` is matched against the known stages rather than interpolated anywhere -- an unknown one is
    a 404, and no part of it reaches SQL.
    """
    window = max(1, min(hours, 24 * 7))
    try:
        detail = stage_detail(db, key, hours=window)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such stage") from None
    audit(logger, "admin.latency_stage", user=admin.id, stage=key, hours=window)
    return detail

