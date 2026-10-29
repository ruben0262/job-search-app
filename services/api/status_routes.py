"""The status page: is each part of this user's pipeline alive, in colours a person can read."""
from __future__ import annotations

import logging
import uuid as _uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from libs import limits
from libs.db.models import CorpusJob, QueueJob, User, UserJobState
from libs.notify import owed, reachable
from services.api.deps import current_user, get_db

logger = logging.getLogger(__name__)
router = APIRouter(tags=["status"])

# How stale the corpus may be before the harvester is presumed stuck.
HARVEST_WARN = timedelta(hours=1)
HARVEST_DOWN = timedelta(hours=4)

# How long a match may wait unsent before delivery reads as stuck. The sweep queues a run within a
# minute of a posting being stored, and a run sends a backlog a message every few seconds, so an hour
# is many times what a working notifier takes.
STALLED_AFTER = timedelta(hours=1)


class StatusComponent(BaseModel):
    key: str
    label: str
    state: Literal["ok", "warn", "down", "off"]
    detail: str


class StatusReport(BaseModel):
    components: list[StatusComponent]
    checked_at: datetime


def _entry(key: str, label: str, state: str, detail: str) -> StatusComponent:
    return StatusComponent(key=key, label=label, state=state, detail=detail)


@router.get("/status")
def status(db: Session = Depends(get_db),
           user_id: _uuid.UUID = Depends(current_user)) -> StatusReport:
    """Every component, one call — the page shows them together or not at all."""
    user = db.get(User, user_id)
    now = datetime.now(UTC)

    return StatusReport(components=[
        _telegram_bot(user),
        _harvester(db, now),
        _delivery(db, user, now),
        _docgen(db, user_id),
    ], checked_at=now)


def _telegram_bot(user: User) -> StatusComponent:
    if user.telegram_status == "connected":
        return _entry("telegram_bot", "Telegram", "ok",
                      "Connected — new matches arrive in your Telegram chat.")
    if user.telegram_status == "broken":
        return _entry("telegram_bot", "Telegram", "down",
                      "Telegram stopped taking our messages. Connect it again in Settings.")
    # Optional, so not a warning.
    return _entry("telegram_bot", "Telegram", "off",
                  "Not connected. Connect it in Settings to get new matches on your phone.")


def _harvester(db: Session, now: datetime) -> StatusComponent:
    """Shared, deliberately: one corpus serves everyone, so its freshness is everyone's answer."""
    last = db.scalar(select(func.max(CorpusJob.first_seen_at)))
    if last is None:
        return _entry("harvester", "Job collection", "down", "No jobs collected yet.")
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    age = now - last
    minutes = int(age.total_seconds() // 60)
    if age > HARVEST_DOWN:
        return _entry("harvester", "Job collection", "down",
                      f"Nothing collected for {minutes // 60} hours. The radar looks stuck.")
    if age > HARVEST_WARN:
        return _entry("harvester", "Job collection", "warn",
                      f"Last new job {minutes} minutes ago — slower than the usual cycle.")
    return _entry("harvester", "Job collection", "ok",
                  f"Collecting. Last new job {minutes} minute{'s' if minutes != 1 else ''} ago.")


def _delivery(db: Session, user: User, now: datetime) -> StatusComponent:
    """Is anything actually arriving — the question every other check assumes and none of them ask."""
    if db.scalar(select(User.id).where(User.id == user.id, reachable())) is None:
        return _entry("delivery", "Delivery", "off",
                      "Connect Telegram or turn on notifications to get new matches off the site.")

    # A pause is a decision, not a fault.
    if not user.active and user.onboarding_completed_at is not None:
        return _entry("delivery", "Delivery", "off",
                      "Paused by you — nothing is being sent. Turn messages back on in Settings.")

    # Today's allowance, spent, is not a fault.
    sent_today = _sent_today(db, user)
    cap = daily_cap(user)
    # Only when a cap exists.
    if cap is not None and sent_today >= cap:
        return _entry("delivery", "Delivery", "ok",
                      f"Today's {cap} jobs have been sent — that is this account's daily limit. "
                      "More arrive tomorrow.")

    # What the notifier sends next, by its own definition, and how long the oldest has waited.
    waiting = owed(db, user)
    if waiting:
        waited = now - waiting[0].job.first_seen_at
        if waited > STALLED_AFTER:
            return _entry("delivery", "Delivery", "down",
                          f"{len(waiting)} matching job(s) waiting, the oldest for "
                          f"{int(waited.total_seconds() // 3600)} hour(s) — delivery looks stuck.")
        return _entry("delivery", "Delivery", "ok",
                      f"Sending — {len(waiting)} new matching job(s) on the way.")

    last_sent = db.scalar(
        select(func.max(UserJobState.notified_at)).where(UserJobState.user_id == user.id))
    if last_sent is None:
        return _entry("delivery", "Delivery", "off",
                      "Nothing sent yet — matches are sent as they are found.")
    if last_sent.tzinfo is None:
        last_sent = last_sent.replace(tzinfo=UTC)

    hours = int((now - last_sent).total_seconds() // 3600)
    if hours >= 24:
        return _entry("delivery", "Delivery", "ok", f"Last delivery {hours // 24} day(s) ago.")
    return _entry("delivery", "Delivery", "ok",
                  f"Sending. Last delivery {hours} hour{'s' if hours != 1 else ''} ago.")


def _docgen(db: Session, user_id: _uuid.UUID) -> StatusComponent:
    last = db.scalar(
        select(QueueJob).where(QueueJob.user_id == user_id, QueueJob.lane == "docgen")
        .order_by(QueueJob.id.desc()).limit(1))
    if last is None:
        return _entry("docgen", "CV & cover letters", "off",
                      "Nothing built yet. Tap Make my CV under a job to try it.")
    if last.state == "dead":
        detail = (last.last_error or "the last build failed")[:160]
        return _entry("docgen", "CV & cover letters", "down", f"The last build failed: {detail}")
    if last.state in ("ready", "claimed"):
        return _entry("docgen", "CV & cover letters", "ok", "A build is in progress right now.")
    return _entry("docgen", "CV & cover letters", "ok", "Working. The last build finished cleanly.")


def daily_cap(user: User) -> int | None:
    """This account's delivery cap, or `None` for unlimited."""
    cap = limits.resolve(user, "notifications_per_day")
    # None travels out as None.
    return cap


def _sent_today(db: Session, user: User) -> int:
    """Deliveries since this user's local midnight, counted the way the notifier counts them."""
    local_midnight = func.date_trunc("day", func.timezone(user.timezone or "UTC", func.now()))
    return db.scalar(
        select(func.count()).select_from(UserJobState)
        .where(UserJobState.user_id == user.id,
               UserJobState.notified_at.is_not(None),
               func.timezone(user.timezone or "UTC", UserJobState.notified_at) >= local_midnight)) or 0
