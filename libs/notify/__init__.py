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
# A document was made but not kept: their storage allowance (`document_storage_mb`) is full.
STORAGE_FULL = Event("documents.storage_full", "warning", "Your document storage is full", "#/documents")
# A Make button pressed without what it needs (`libs.documents.needs`, owner 2026-11-10): where the first
# step is, their AI account in Settings, else the CV or cover letter in My documents.
NEEDS_AI_ACCOUNT = Event("document.needs_ai_account", "warning", "Link your AI account to make documents",
                         "#/settings")
NEEDS_UPLOAD = Event("document.needs_upload", "warning", "Upload what your documents are made from",
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
    return {"$or": [{"telegram_status": "connected"}, {"_id": {"$in": browsers}}, {"email_alerts": True}]}


def sent_up_to(db: Database, user: User) -> datetime:
    """When the newest job already sent to this person had its whole text — or, if later, when the
    alert channels they have now were turned on.

    A job is alerted once it has its description, because that is a whole job (owner, 2026-11-02):
    LinkedIn's are found without one and described minutes later. So what is new is what was
    described after this, not what was found after it, or a job found early and described late
    would fall behind the watermark and never be sent. Alerts announce what is new: somebody
    switching them on weeks after joining gets what is described from then on, not every match
    still inside its freshness window at once — the jobs page already shows those.
    """
    sent = list(db[UserJobState.COLLECTION].aggregate([
        {"$match": {"user_id": user.id, "notified_at": {"$ne": None}}},
        {"$lookup": {"from": CorpusJob.COLLECTION, "localField": "job_id", "foreignField": "_id",
                     "pipeline": [{"$project": {"described_at": 1}}], "as": "job"}},
        {"$unwind": "$job"},
        {"$group": {"_id": None, "newest": {"$max": "$job.described_at"}}}]))
    newest = sent[0]["newest"] if sent else None
    # When their Telegram chat was connected, and their oldest browser.
    chat = user.telegram_connected_at if user.telegram_status == "connected" else None
    browser = db[PushSubscription.COLLECTION].find_one(
        {"user_id": user.id}, {"created_at": 1}, sort=[("created_at", 1)])
    turned_on = min(filter(None, (chat, browser and browser["created_at"])), default=None)
    return max(filter(None, (newest or user.created_at, turned_on)))


def sent_today(db: Database, user_id: _uuid.UUID) -> int:
    """Jobs sent to this person since their own midnight, which the notifier's daily cap counts."""
    return db[UserJobState.COLLECTION].count_documents(
        {"user_id": user_id, "notified_at": {"$gte": limits.their_midnight(db, user_id)}})


# How many new candidates one delivery examines — five times the jobs page's scan, because the
# watermark moves past whatever it does not reach. A backlog beyond it is logged, not lost quietly.
NOTIFY_SCAN = 10_000


def owed(db: Database, user: User) -> list[Match]:
    """The matches with their whole text not yet sent to this person, in the order they had it — the
    order they are sent in. With that text, which the Telegram card reads its salary line from.

    From the watermark inclusive, and less what was sent: two jobs described in the same millisecond
    share it, and neither may be skipped."""
    sent = set(db[UserJobState.COLLECTION].distinct(
        "job_id", {"user_id": user.id, "notified_at": {"$ne": None}}))
    return sorted((match for match in matching_jobs(db, user, described_since=sent_up_to(db, user),
                                                    scan=NOTIFY_SCAN, text=True)
                   if match.job.id not in sent),
                  key=lambda match: match.job.described_at)
