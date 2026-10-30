"""The bell: list, unread count, mark read."""
from __future__ import annotations

import logging
import uuid as _uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from pymongo.database import Database

from libs.db import find, now
from libs.db.models import UserNotification
from services.api.deps import current_user, get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/notifications", tags=["notifications"])


class NotificationRow(BaseModel):
    id: int
    event: str
    level: str
    title: str
    body: str
    # An in-app route, like "#/settings": where opening it takes them.
    url: str | None
    created_at: str | None
    read: bool


class NotificationList(BaseModel):
    unread: int
    notifications: list[NotificationRow]


@router.get("", response_model=NotificationList)
def list_notifications(db: Database = Depends(get_db),
                       user_id: _uuid.UUID = Depends(current_user),
                       limit: int = Query(20, ge=1, le=100)) -> dict:
    """The panel's list plus the bell's count, in one call — they are always shown together."""
    rows = find(db, UserNotification, {"user_id": user_id}, sort=[("_id", -1)], limit=limit)
    unread = db[UserNotification.COLLECTION].count_documents({"user_id": user_id, "read_at": None})
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
def mark_read(body: MarkRead, db: Database = Depends(get_db),
              user_id: _uuid.UUID = Depends(current_user)) -> dict:
    where: dict = {"user_id": user_id, "read_at": None}
    if not body.all:
        if not body.ids:
            return {"marked": 0}
        where["_id"] = {"$in": body.ids}
    return {"marked": db[UserNotification.COLLECTION].update_many(
        where, {"$set": {"read_at": now()}}).modified_count}


class Discard(BaseModel):
    # Same shape as `MarkRead`: the bin must also reach what scrolled past the limit.
    ids: list[int] | None = None
    all: bool = False


@router.post("/delete")
def delete_notifications(body: Discard, db: Database = Depends(get_db),
                         user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Dismiss notifications for good."""
    where: dict = {"user_id": user_id}
    if not body.all:
        if not body.ids:
            return {"deleted": 0}
        where["_id"] = {"$in": body.ids}
    return {"deleted": db[UserNotification.COLLECTION].delete_many(where).deleted_count}
