"""The bell: list, unread count, mark read."""
from __future__ import annotations

import logging
import uuid as _uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from libs.db.models import UserNotification
from services.api.deps import current_user, get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
def list_notifications(db: Session = Depends(get_db),
                       user_id: _uuid.UUID = Depends(current_user),
                       limit: int = Query(20, ge=1, le=100)) -> dict:
    """The panel's list plus the bell's count, in one call — they are always shown together."""
    rows = db.scalars(
        select(UserNotification)
        .where(UserNotification.user_id == user_id)
        .order_by(UserNotification.id.desc())
        .limit(limit)).all()
    unread = db.scalar(
        select(func.count()).select_from(UserNotification)
        .where(UserNotification.user_id == user_id, UserNotification.read_at.is_(None)))
    return {
        "unread": unread,
        "notifications": [{
            "id": row.id,
            "event": row.event,
            "level": row.level,
            "title": row.title,
            "body": row.body,
            "url": row.url,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "read": row.read_at is not None,
        } for row in rows],
    }


class MarkRead(BaseModel):
    # Explicit ids, or everything.
    ids: list[int] | None = None
    all: bool = False


@router.post("/read")
def mark_read(body: MarkRead, db: Session = Depends(get_db),
              user_id: _uuid.UUID = Depends(current_user)) -> dict:
    query = db.query(UserNotification).filter(UserNotification.user_id == user_id,
                                              UserNotification.read_at.is_(None))
    if not body.all:
        if not body.ids:
            return {"marked": 0}
        query = query.filter(UserNotification.id.in_(body.ids))
    marked = query.update({"read_at": func.now()}, synchronize_session=False)
    return {"marked": marked or 0}


class Discard(BaseModel):
    # Same shape as `MarkRead`: the bin must also reach what scrolled past the limit.
    ids: list[int] | None = None
    all: bool = False


@router.post("/delete")
def delete_notifications(body: Discard, db: Session = Depends(get_db),
                         user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Dismiss notifications for good."""
    query = db.query(UserNotification).filter(UserNotification.user_id == user_id)
    if not body.all:
        if not body.ids:
            return {"deleted": 0}
        query = query.filter(UserNotification.id.in_(body.ids))
    deleted = query.delete(synchronize_session=False)
    return {"deleted": deleted or 0}
