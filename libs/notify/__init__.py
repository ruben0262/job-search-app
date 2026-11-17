"""Telling a person something: an in-app notification, and the jobs their alerts caught that are owed to them.

What is owed is defined once, here (`owed`): what `libs.alerts` caught and the notifier has not sent. It stays
in
`libs` beside the in-app notifications the API writes.
"""
from __future__ import annotations

import logging
import os
import uuid as _uuid
from dataclasses import dataclass

from pymongo.database import Database

from libs import limits
from libs.db import insert, now
from libs.db.models import Alert, PushSubscription, User, UserJobState, UserNotification

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Event:
    slug: str
    level: str
    title: str
    # Where clicking it takes the user. None when there is nowhere better than where they are.
    url: str | None = None


# The catalogue. Adding one here is the whole registration.
TELEGRAM_BROKEN = Event("telegram.broken", "error", "Telegram delivery stopped", "#/alerts")
PASSWORD_CHANGED = Event("account.password_changed", "warning", "Your password was changed")
# A build was asked for and cannot happen yet.
DOCUMENT_NOT_READY = Event("document.not_ready", "warning", "A document could not be built",
                           "#/documents")
# A document was made but not kept: their storage allowance (`document_storage_mb`) is full.
STORAGE_FULL = Event("documents.storage_full", "warning", "Your document storage is full", "#/documents")
# A Make button pressed without what it needs (`libs.documents.needs`, owner 2026-11-10): where the first
# step is, their AI account in Settings, else the CV or cover letter in My documents.
NEEDS_AI_ACCOUNT = Event("document.needs_ai_account", "warning", "Link your AI account to make documents",
                         "#/settings")
NEEDS_UPLOAD = Event("document.needs_upload", "warning", "Upload what your documents are made from",
                     "#/documents")
# A Make button pressed by a free account (owner, 2026-11-12: the documents are a member's).
NEEDS_MEMBERSHIP = Event("document.needs_membership", "info",
                         "Making CVs and cover letters comes with a membership", "#/upgrade")

def notify(db: Database, user_id: _uuid.UUID, event: Event, body: str = "") -> None:
    """Record one. An unread one of the same kind is brought up to date rather than repeated."""
    try:
        refreshed = db[UserNotification.COLLECTION].update_one(
            {"user_id": user_id, "event": event.slug, "read_at": None},
            {"$set": {"body": body, "created_at": now()}})
        if not refreshed.matched_count:
            insert(db, UserNotification(user_id=user_id, event=event.slug, level=event.level,
                                        title=event.title, body=body, url=event.url))
    except Exception:
        logger.warning("could not record notification %s for %s", event.slug, user_id,
                       exc_info=True)


def site_url(route: str = "") -> str:
    """An address on the site, for anything that leaves it: an email, a Telegram button, an alert."""
    return os.environ.get("PUBLIC_URL", "http://localhost:8000").rstrip("/") + route


def reachable(db: Database, user: User) -> bool:
    """Whether an alert can reach this person off the site: their Telegram, a browser that subscribed, or an
    alert of theirs with the daily email."""
    return (user.telegram_status == "connected"
            or db[PushSubscription.COLLECTION].count_documents({"user_id": user.id}, limit=1) > 0
            or db[Alert.COLLECTION].count_documents({"user_id": user.id, "on": True, "channels": "email"},
                                                    limit=1) > 0)


def sent_today(db: Database, user_id: _uuid.UUID) -> int:
    """Jobs sent to this person since their own midnight, which the notifier's daily cap counts."""
    return db[UserJobState.COLLECTION].count_documents(
        {"user_id": user_id, "notified_at": {"$gte": limits.their_midnight(db, user_id)}})


def owed(db: Database, user_id: _uuid.UUID) -> list[dict]:
    """What their alerts caught (`libs.alerts.route`) and the notifier has not sent yet, in the order it was
    caught, which is the order it is sent in: `{"job_id": ..., "alerts": [...]}` each. One definition, read
    by the notifier."""
    return list(db[UserJobState.COLLECTION].find(
        {"user_id": user_id, "alerted_at": {"$ne": None}, "notified_at": None},
        {"_id": 0, "job_id": 1, "alerts": 1}, sort=[("alerted_at", 1), ("job_id", 1)]))
