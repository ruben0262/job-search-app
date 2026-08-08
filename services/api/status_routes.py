"""The status page: is each part of this user's pipeline alive, in colours a person can read."""
from __future__ import annotations

import logging
import uuid as _uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from libs import limits
from libs.db.models import CorpusJob, QueueJob, User, UserJobState, UserModel
from libs.scoring.thresholds import cutoff_for
from libs.tenant.filters import pending_match_conditions
from services.api.deps import current_user, get_db

logger = logging.getLogger(__name__)
router = APIRouter(tags=["status"])

# How stale the corpus may be before the harvester is presumed stuck.
HARVEST_WARN = timedelta(hours=1)
HARVEST_DOWN = timedelta(hours=4)

# How long matches may sit undelivered before it stops being quiet and starts being broken.
DELIVERY_WARN_HOURS = 3
DELIVERY_DOWN_HOURS = 6


def _entry(key: str, label: str, state: str, detail: str) -> dict:
    return {"key": key, "label": label, "state": state, "detail": detail}


@router.get("/status")
def status(db: Session = Depends(get_db),
           user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Every component, one call — the page shows them together or not at all."""
    user = db.get(User, user_id)
    now = datetime.now(UTC)

    return {"components": [
        _telegram_bot(user),
        _telegram_channel(user),
        _harvester(db, now),
        _scorer(db, user, now),
        _delivery(db, user, now),
        _docgen(db, user_id),
    ], "checked_at": now.isoformat()}


def _telegram_bot(user: User) -> dict:
    if user.telegram_status == "connected":
        return _entry("telegram_bot", "Telegram bot", "ok", "Connected and able to message you.")
    if user.telegram_status == "broken":
        return _entry("telegram_bot", "Telegram bot", "down",
                      "The bot token stopped working. Save a fresh one under Your details.")
    return _entry("telegram_bot", "Telegram bot", "warn",
                  "Not connected yet — save a bot token and press Start in the bot.")


def _telegram_channel(user: User) -> dict:
    if user.telegram_routes and any(user.telegram_routes.values()):
        return _entry("telegram_channel", "Telegram channel", "ok",
                      "Jobs stream to your channel; the bot chat stays for commands.")
    return _entry("telegram_channel", "Telegram channel", "off",
                  "No channel set — jobs arrive in your bot chat. Add one under Your details.")


def _harvester(db: Session, now: datetime) -> dict:
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


def _scorer(db: Session, user: User, now: datetime) -> dict:
    model = db.scalar(
        select(UserModel).where(UserModel.user_id == user.id, UserModel.is_active.is_(True))
        .limit(1))
    if model is None:
        return _entry("scorer", "Scoring", "off",
                      "No trained model yet — the feed ranks by your keywords until there is one.")

    stale = model.profile_generation != user.profile_generation
    last_scored = db.scalar(
        select(func.max(UserJobState.scored_at)).where(UserJobState.user_id == user.id))
    if stale:
        return _entry("scorer", "Scoring", "warn",
                      "Your profile changed after this model was trained — retrain it from the "
                      "Model page.")
    if last_scored is None:
        return _entry("scorer", "Scoring", "warn",
                      "A model exists but nothing has been scored yet. The next cycle should.")
    if last_scored.tzinfo is None:
        last_scored = last_scored.replace(tzinfo=UTC)
    hours = int((now - last_scored).total_seconds() // 3600)
    if hours >= 4:
        return _entry("scorer", "Scoring", "warn",
                      f"Last score {hours} hours ago — slower than the usual cycle.")
    return _entry("scorer", "Scoring", "ok",
                  f"Model v{model.version}, scoring new jobs as they arrive.")


def _delivery(db: Session, user: User, now: datetime) -> dict:
    """Is anything actually arriving — the question every other check assumes and none of them ask."""
    if user.telegram_status != "connected":
        return _entry("delivery", "Delivery", "off",
                      "Connect Telegram and matches will start arriving.")

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

    cutoff = cutoff_for(db, user.id)
    waiting = db.scalar(
        select(func.count()).select_from(UserJobState)
        .join(CorpusJob, CorpusJob.id == UserJobState.job_id)
        .where(UserJobState.user_id == user.id,
               # The notifier's own definition of "still owed", not a second copy of it.
               *pending_match_conditions(cutoff),
               CorpusJob.closed_at.is_(None))) or 0
    last_sent = db.scalar(
        select(func.max(UserJobState.notified_at)).where(UserJobState.user_id == user.id))
    if last_sent is not None and last_sent.tzinfo is None:
        last_sent = last_sent.replace(tzinfo=UTC)

    if last_sent is None:
        # Never delivered. Only a problem once there is something to deliver.
        if waiting:
            return _entry("delivery", "Delivery", "down",
                          f"{waiting} matches are waiting and none have been sent yet.")
        return _entry("delivery", "Delivery", "off",
                      "Nothing sent yet — no matches have cleared your score cutoff.")

    hours = int((now - last_sent).total_seconds() // 3600)
    if waiting and hours >= DELIVERY_DOWN_HOURS:
        return _entry("delivery", "Delivery", "down",
                      f"{waiting} matches waiting and nothing sent for {hours} hours.")
    if waiting and hours >= DELIVERY_WARN_HOURS:
        return _entry("delivery", "Delivery", "warn",
                      f"{waiting} matches waiting; last delivery {hours} hours ago.")
    if hours >= 24:
        # Quiet, but nothing is queued behind it — a thin week, not a broken pipeline.
        return _entry("delivery", "Delivery", "ok",
                      f"Nothing new to send. Last delivery {hours // 24} day(s) ago.")
    return _entry("delivery", "Delivery", "ok",
                  f"Sending. Last delivery {hours} hour{'s' if hours != 1 else ''} ago.")


def _docgen(db: Session, user_id: _uuid.UUID) -> dict:
    last = db.scalar(
        select(QueueJob).where(QueueJob.user_id == user_id, QueueJob.lane == "docgen")
        .order_by(QueueJob.id.desc()).limit(1))
    if last is None:
        return _entry("docgen", "CV & cover letters", "off",
                      "Nothing built yet. Tap Build CV under a job in Telegram to try it.")
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
