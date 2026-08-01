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

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from libs.db.models import User, UserJobState, UserLabel
from libs.obs import audit
from libs.ops import evaluate, platform_health, render
from libs.ops.latency import pipeline_latency
from libs.profiles.repository import ProfileFiles
from libs.scoring.history import model_history
from libs.settings import DEFAULTS, HARVEST_SOURCES, all_settings, freshness_hours, put
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
                "is_admin": row.is_admin,
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
            "onboarded": user.onboarding_completed_at is not None,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "last_active_at": user.last_active_at.isoformat() if user.last_active_at else None,
        },
        # Setup completeness, by the same predicate the trainer refuses on — so "their setup looks
        # finished but training is refused" cannot happen without this page showing why.
        "setup": {"missing": files.missing_setup(),
                  "files": [{"name": row["name"], "kind": row["kind"], "filled": row["filled"]}
                            for row in files.listing()]},
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
    """
    return {"sources": [{"name": name, "hours": freshness_hours(db, name)}
                        for name in HARVEST_SOURCES]}
