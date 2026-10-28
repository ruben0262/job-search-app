"""Telling a person something: an in-app notification, and the new matches owed to them off the site.

What is owed is defined once, here, because two places ask: the notifier sends it and the status page
counts it. `services` may not import each other, so the definition lives in `libs` (CLAUDE.md §5).
"""
from __future__ import annotations

import logging
import os
import uuid as _uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

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
TELEGRAM_BROKEN = Event("telegram.broken", "error", "Telegram delivery stopped", "#/data")
MODEL_TRAINED = Event("model.trained", "info", "Your model was retrained")
PASSWORD_CHANGED = Event("account.password_changed", "warning", "Your password was changed")
# A build was asked for and cannot happen yet.
DOCUMENT_NOT_READY = Event("document.not_ready", "warning", "A document could not be built",
                           "#/setup")
def notify(session: Session, user_id: _uuid.UUID, event: Event, body: str = "") -> None:
    """Record one."""
    try:
        existing = session.scalar(
            select(UserNotification)
            .where(UserNotification.user_id == user_id,
                   UserNotification.event == event.slug,
                   UserNotification.read_at.is_(None))
            .limit(1))
        if existing is not None:
            existing.body = body
            existing.created_at = func.now()
            return
        session.add(UserNotification(user_id=user_id, event=event.slug, level=event.level,
                                     title=event.title, body=body, url=event.url))
    except Exception:
        logger.warning("could not record notification %s for %s", event.slug, user_id,
                       exc_info=True)


def site_url(route: str = "") -> str:
    """An address on the site, for anything that leaves it: an email, a Telegram button, an alert."""
    return os.environ.get("PUBLIC_URL", "http://localhost:8000").rstrip("/") + route


def reachable():
    """The accounts an alert can reach: Telegram connected, or at least one browser subscribed."""
    return or_(User.telegram_status == "connected",
               User.id.in_(select(PushSubscription.user_id)))


def sent_up_to(session: Session, user: User) -> datetime:
    """When the newest job already sent to this person was first seen — or, if later, when the alert
    channels they have now were turned on.

    Every alert is a match first seen after this, so a run stopped part-way resumes where it stopped.
    Alerts announce what is new: somebody switching them on weeks after joining, or again after
    turning them off, gets what is found from then on, not every match still inside its freshness
    window at once — the jobs page already shows those. Strictly after: `first_seen_at` is
    `clock_timestamp()`, one value per row, so no unsent job shares the newest sent one's timestamp.
    """
    newest = session.scalar(
        select(func.max(CorpusJob.first_seen_at))
        .join(UserJobState, UserJobState.job_id == CorpusJob.id)
        .where(UserJobState.user_id == user.id, UserJobState.notified_at.is_not(None)))
    # Derived, not stored: the code that linked their current Telegram chat, and their oldest browser.
    linked = session.scalar(
        select(func.max(AuthToken.used_at))
        .where(AuthToken.user_id == user.id, AuthToken.purpose == "telegram_connect")
    ) if user.telegram_status == "connected" else None
    subscribed = session.scalar(
        select(func.min(PushSubscription.created_at)).where(PushSubscription.user_id == user.id))
    turned_on = min(filter(None, (linked, subscribed)), default=None)
    return max(filter(None, (newest or user.created_at, turned_on)))


def owed(session: Session, user: User) -> list[Match]:
    """The matches not yet sent to this person, oldest first — the order they are sent in."""
    return sorted(matching_jobs(session, user, since=sent_up_to(session, user)),
                  key=lambda match: match.job.first_seen_at)
