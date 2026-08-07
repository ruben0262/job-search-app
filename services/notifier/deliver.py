"""Delivering one user's feed to their own Telegram bot."""
from __future__ import annotations

import logging
import statistics
import uuid as _uuid
from collections.abc import Callable
from datetime import UTC, datetime
from time import monotonic, sleep

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

import libs.notify as notifylib
from libs import limits
from libs.db.models import CorpusJob, QueueJob, User, UserJobState
from libs.db.models.queue import CLAIM_TIMEOUT_MIN
from libs.obs import audit
from libs.queue import enqueue
from libs.scoring.thresholds import DEFAULT_CUTOFF, cutoff_for
from libs.secrets import SecretStore
from services.notifier.buttons import keyboard
from services.notifier.card import FeedItem
from services.notifier.card import render as render_card
from services.notifier.eligibility import CANDIDATE_MULTIPLIER, rank
from services.notifier.routing import group_by_chat

logger = logging.getLogger(__name__)

# The whole claim is the send budget now that delivery fetches nothing: no page is read, no proxy is
# opened, and the only work between claiming and sending is one indexed query.
SEND_BUDGET_SECONDS = CLAIM_TIMEOUT_MIN["notify"] * 60 * 0.6


def unlimited_items(gap_seconds: float) -> int:
    """The most one run could send if nothing stopped it, plus one to prove there is more."""
    return int(SEND_BUDGET_SECONDS / max(gap_seconds, 0.1)) + 1

# Re-exported, not redefined.

# Telegram's answers when the bot is gone.
TERMINAL_ERRORS = ("bot was blocked", "chat not found", "user is deactivated",
                   "bot was kicked", "unauthorized")


class BotRevoked(RuntimeError):
    """The user's bot is gone. Terminal — flag the account, do not retry."""


def pending_feed(session: Session, user_id: _uuid.UUID, *, cutoff: float,
                 limit: int) -> list[FeedItem]:
    """This user's next digest: everything scored above their cutoff, best first, capped at `limit`.

    No filter runs here. Whether a posting is worth this account's attention was decided once, in
    `libs.tenant.filters` before scoring, and asking again at send time is how one question came to
    have two answers — a health page counting jobs a filter it could not see was dropping.
    """
    rows = session.execute(
        select(UserJobState.job_id, CorpusJob.url, CorpusJob.title, CorpusJob.company,
               UserJobState.score, CorpusJob.source, CorpusJob.location, CorpusJob.is_remote,
               CorpusJob.description, CorpusJob.posted_at, CorpusJob.applicants,
               # Positional.
               CorpusJob.applicants_caption,
               CorpusJob.easy_apply, CorpusJob.reposted, CorpusJob.workplace,
               CorpusJob.first_seen_at)
        .join(CorpusJob, CorpusJob.id == UserJobState.job_id)
        .where(UserJobState.user_id == user_id,
               UserJobState.notified_at.is_(None),
               UserJobState.score.is_not(None),
               UserJobState.score >= cutoff)
        # Known-fresh first, then undated, and only then by score.
        .order_by(CorpusJob.posted_at.is_(None), UserJobState.score.desc())
        .limit(limit * CANDIDATE_MULTIPLIER)).all()

    items = [FeedItem(*row[:-1]) for row in rows]
    first_seen = {row[0]: row[-1] for row in rows}
    return rank(items, first_seen=first_seen)[:limit]


def notifications_today(session: Session, user_id: _uuid.UUID) -> int:
    """How many items have been delivered since the user's local midnight."""
    timezone = session.scalar(select(User.timezone).where(User.id == user_id)) or "UTC"
    local_midnight = func.date_trunc("day", func.timezone(timezone, func.now()))
    return session.scalar(
        select(func.count())
        .select_from(UserJobState)
        .where(UserJobState.user_id == user_id,
               UserJobState.notified_at.is_not(None),
               func.timezone(timezone, UserJobState.notified_at) >= local_midnight)) or 0


def deliver(session: Session, user_id: _uuid.UUID, *,
            store: SecretStore,
            send: Callable[..., None],
            cutoff: float = DEFAULT_CUTOFF) -> int:
    """Send this user's pending feed."""
    user = session.get(User, user_id)
    if user is None or user.telegram_status != "connected" or not user.telegram_chat_id:
        return 0

    cutoff = _operating_point(session, user_id, cutoff)

    # The operator's ceilings for this account, and `None` means unlimited (`libs.limits`).
    gap, limit = _run_budget(session, user)
    if limit == 0:
        return 0

    items = pending_feed(session, user_id, cutoff=cutoff, limit=limit)
    if not items:
        return 0

    # One digest per channel.
    batches = group_by_chat(items, user.telegram_routes, user.telegram_chat_id)

    sent = 0
    deadline = monotonic() + SEND_BUDGET_SECONDS
    with store.use(user_id, "telegram_bot") as token:
        if token is None:
            raise BotRevoked(f"user {user_id} has no telegram token stored")
        for chat_id, batch in batches.items():
            try:
                # Cards only, no header.
                for item in batch:
                    # Stop before the claim does.
                    if monotonic() >= deadline:
                        # Straight back into the queue, not "next cycle".
                        _deliver_again(session, user_id)
                        logger.info(
                            "notify: %s sent %d and ran out of its send budget — continuing "
                            "immediately", user_id, sent)
                        return sent
                    # Paced, because Telegram allows 20 messages a minute to a channel.
                    if sent:
                        sleep(gap)
                    send(token.reveal(), chat_id, render_job(item, cutoff=cutoff),
                         keyboard(item.job_id))
                    # Marked and committed the moment it has gone, one message at a time.
                    session.get(UserJobState, (user_id, item.job_id)).notified_at = func.now()
                    session.commit()
                    sent += 1
            except Exception as exc:
                if _is_terminal(exc):
                    # A revoked token is terminal for every channel, so stopping here is right.
                    user.telegram_status = "broken"
                    # The one failure Telegram itself cannot tell them about.
                    notifylib.notify(session, user_id, notifylib.TELEGRAM_BROKEN,
                                     "Your bot token stopped working, so jobs are not being "
                                     "delivered. Save a fresh token under Your details.")
                    raise BotRevoked(str(exc)[:200]) from exc
                raise

    # A full fetch means the limit bound rather than the supply, so there is more behind it.
    if len(items) >= limit:
        _deliver_again(session, user_id)

    _log_freshness(session, user_id, items[:sent])
    return sent


def _run_budget(session: Session, user: User) -> tuple[float, int]:
    """How fast this run may send, and how many rows are worth fetching for it."""
    # Never None: `send_gap_seconds` is Telegram's rule rather than ours and forbids unlimited.
    gap = limits.resolve(user, "send_gap_seconds") or limits.DEFAULT_SEND_GAP_SECONDS
    per_run = limits.resolve(user, "items_per_run")
    per_day = limits.resolve(user, "notifications_per_day")

    if per_day is not None:
        remaining = per_day - notifications_today(session, user.id)
        if remaining <= 0:
            logger.info("notify: %s is at their daily cap, deferring", user.id)
            return gap, 0
        # A daily ceiling bounds an unlimited per-run one; two unlimiteds stay unlimited.
        per_run = remaining if per_run is None else min(per_run, remaining)

    return gap, per_run if per_run is not None else unlimited_items(gap)


def _deliver_again(session: Session, user_id: _uuid.UUID) -> None:
    """Queue the rest of this account's feed now, rather than waiting for the dispatcher's tick.

    The dispatcher runs once a minute, so leaving the continuation to it spent up to 60 seconds of
    every ~140 idle with work waiting. `next_run_at` is still moved as the backstop for the case
    where this enqueue collapses against a job somebody else already queued.
    """
    session.execute(update(User).where(User.id == user_id).values(next_run_at=func.now()))

    # This run's own job still holds the `due` slot, and the dedup index refuses a second one while
    # it does. Released through the mapped row, so the worker's later `complete` cannot write it back.
    running = session.scalar(
        select(QueueJob).where(QueueJob.lane == "notify", QueueJob.user_id == user_id,
                               QueueJob.state == "claimed", QueueJob.dedup_key == "due"))
    if running is not None:
        running.dedup_key = None
    session.flush()
    enqueue(session, "notify", user_id, dedup_key="due")


def _operating_point(session: Session, user_id: _uuid.UUID, requested: float) -> float:
    """The score this user's jobs are sent at."""
    if requested != DEFAULT_CUTOFF:
        return requested
    return cutoff_for(session, user_id)


def _log_freshness(session: Session, user_id: _uuid.UUID, items: list[FeedItem]) -> None:
    """How stale the feed was, measured at the only point where the answer is real."""
    job_ids = [item.job_id for item in items]
    if not job_ids:
        return

    rows = session.execute(
        select(CorpusJob.posted_at, CorpusJob.first_seen_at)
        .where(CorpusJob.id.in_(job_ids))).all()
    now = datetime.now(UTC)

    def minutes(values) -> int | None:
        ages = [(now - value).total_seconds() / 60 for value in values if value is not None]
        return round(statistics.median(ages)) if ages else None

    # Median, not mean: one job dated three weeks ago drags a mean into meaninglessness.
    audit(logger, "notify.freshness", user=user_id, items=len(job_ids),
          median_age_from_posted_min=minutes(row[0] for row in rows),
          median_age_from_first_seen_min=minutes(row[1] for row in rows))


def render_job(item: FeedItem, salary_bands: dict | None = None,
               cutoff: float = DEFAULT_CUTOFF) -> str:
    """One job, as its own message, so it can carry its own buttons."""
    return render_card(item, salary_bands=salary_bands, high_fit=cutoff)


def _is_terminal(exc: Exception) -> bool:
    message = str(exc).casefold()
    return any(marker in message for marker in TERMINAL_ERRORS)
