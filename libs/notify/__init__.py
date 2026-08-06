"""Producing an in-app notification, from any service.

One function, `notify`, called at the moment a service learns something the user should know —
a bot token stopped working, a training run finished, the password changed. The dashboard's bell
polls what accumulates here.

**Collapse is the default.** Every producer in this platform runs on a timer, so any persistent
fact ("Telegram is broken") would otherwise be re-announced every cycle; forty copies of the same
truth is how a bell gets ignored and the one real error scrolls away. An unread notification for
the same event is refreshed in place — its timestamp moves, its body updates — and only a *read*
one allows a new unread row, which is exactly the reader's view: "this is still true" versus "this
happened again since you looked".

Templates are here rather than at call sites (FlowSolutions' `events.go` arrangement) so two
services reporting the same event cannot word it two ways.
"""
from __future__ import annotations

import logging
import uuid as _uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from libs.db.models import UserNotification

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
PROCESSING_DONE = Event("processing.done", "info", "Your profile is ready to review", "#/process")
PROCESSING_FAILED = Event("processing.failed", "error", "Processing your documents failed",
                          "#/process")
PASSWORD_CHANGED = Event("account.password_changed", "warning", "Your password was changed")
# A build was asked for and cannot happen yet. **The user is the only one who can fix it**, so they
# are the one told — this used to dead-letter to the operator and say nothing to anybody.
DOCUMENT_NOT_READY = Event("document.not_ready", "warning", "A document could not be built",
                           "#/setup")
def notify(session: Session, user_id: _uuid.UUID, event: Event, body: str = "") -> None:
    """Record one. Never raises — a notification about a failure must not become a second failure
    inside the same transaction as the recovery it is describing."""
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
