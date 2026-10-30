"""Telling a person something: an in-app notification, and the new matches owed to them off the site.

What is owed is defined once, here. The notifier is its one reader since the status page was deleted
(2026-10-30); it stays in `libs` beside the in-app notifications the API writes.
"""
from __future__ import annotations

import logging
import os
import uuid as _uuid
from dataclasses import dataclass
from datetime import datetime

from pymongo.database import Database

from libs import limits
from libs.db import insert, now
from libs.db.models import (
    AuthToken,
    CorpusJob,
    PushSubscription,
    User,
    UserJobState,
    UserNotification,
)
from libs.matching import Match, matching_jobs

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Event:
    slug: str
    level: str
    title: str
    # Where clicking it takes the user. None when there is nowhere better than where they are.
    url: str | None = None


# The catalogue. Adding one here is the whole registration.
TELEGRAM_BROKEN = Event("telegram.broken", "error", "Telegram delivery stopped", "#/settings")
PASSWORD_CHANGED = Event("account.password_changed", "warning", "Your password was changed")
# A build was asked for and cannot happen yet.
DOCUMENT_NOT_READY = Event("document.not_ready", "warning", "A document could not be built",
                           "#/documents")
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


def reachable(db: Database, only: _uuid.UUID | None = None) -> dict:
    """The accounts an alert can reach, as a filter on `users`: Telegram connected, or at least one
    browser subscribed. `only` narrows the browser lookup to one account, for a page asking about its own."""
    browsers = db[PushSubscription.COLLECTION].distinct("user_id", {} if only is None else {"user_id": only})
    return {"$or": [{"telegram_status": "connected"}, {"_id": {"$in": browsers}}]}


def sent_up_to(db: Database, user: User) -> datetime:
    """When the newest job already sent to this person was first seen — or, if later, when the alert
    channels they have now were turned on.

    Every alert is a match first seen after this, so a run stopped part-way resumes where it stopped.
    Alerts announce what is new: somebody switching them on weeks after joining, or again after
    turning them off, gets what is found from then on, not every match still inside its freshness
    window at once — the jobs page already shows those. Strictly after: a posting's `first_seen_at`
    is the moment it was stored, so no unsent job shares the newest sent one's timestamp.
    """
    sent = list(db[UserJobState.COLLECTION].aggregate([
        {"$match": {"user_id": user.id, "notified_at": {"$ne": None}}},
        {"$lookup": {"from": CorpusJob.COLLECTION, "localField": "job_id", "foreignField": "_id",
                     "pipeline": [{"$project": {"first_seen_at": 1}}], "as": "job"}},
        {"$unwind": "$job"},
        {"$group": {"_id": None, "newest": {"$max": "$job.first_seen_at"}}}]))
    newest = sent[0]["newest"] if sent else None
    # Derived, not stored: the code that linked their current Telegram chat, and their oldest browser.
    link = db[AuthToken.COLLECTION].find_one(
        {"user_id": user.id, "purpose": "telegram_connect", "used_at": {"$ne": None}},
        {"used_at": 1}, sort=[("used_at", -1)]) if user.telegram_status == "connected" else None
    browser = db[PushSubscription.COLLECTION].find_one(
        {"user_id": user.id}, {"created_at": 1}, sort=[("created_at", 1)])
    turned_on = min(filter(None, (link and link["used_at"], browser and browser["created_at"])),
                    default=None)
    return max(filter(None, (newest or user.created_at, turned_on)))


def sent_today(db: Database, user_id: _uuid.UUID) -> int:
    """Jobs sent to this person since their own midnight, which the notifier's daily cap counts."""
    return db[UserJobState.COLLECTION].count_documents(
        {"user_id": user_id, "notified_at": {"$gte": limits.their_midnight(db, user_id)}})


# How many new candidates one delivery examines — five times the jobs page's scan, because the
# watermark moves past whatever it does not reach. A backlog beyond it is logged, not lost quietly.
NOTIFY_SCAN = 10_000


def owed(db: Database, user: User) -> list[Match]:
    """The matches not yet sent to this person, oldest first — the order they are sent in."""
    return sorted(matching_jobs(db, user, since=sent_up_to(db, user), scan=NOTIFY_SCAN),
                  key=lambda match: match.job.first_seen_at)
