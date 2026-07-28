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
from sqlalchemy import select
from sqlalchemy.orm import Session

from libs.db.models import User
from libs.obs import audit
from libs.ops import evaluate, platform_health, render
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
    if user is None or not allowed or user.email.lower() not in allowed:
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
                "onboarded": row.onboarding_completed_at is not None,
                "processed_at": row.processed_at.isoformat() if row.processed_at else None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in db.scalars(select(User).order_by(User.created_at))],
    }
