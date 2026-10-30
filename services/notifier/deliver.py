"""Sending a person their new matches: to their Telegram chat, and to every browser they subscribed.

What is new is `libs.notify.owed` — their `libs.matching` matches first seen after the newest one
already sent — so the alerts ask the jobs page's own question and keep no list of their own.
"""
from __future__ import annotations

import logging
import statistics
import uuid as _uuid
from collections.abc import Callable
from time import monotonic, sleep

from pymongo.database import Database

from libs import limits
from libs.db import find, get, now
from libs.db.models import PushSubscription, QueueJob, User
from libs.db.models.queue import CLAIM_TIMEOUT_MIN
from libs.db.models.tenant import record_state
from libs.matching import Match
from libs.notify import TELEGRAM_BROKEN, notify, owed, sent_today
from libs.obs import audit
from libs.queue import enqueue
from services.notifier.card import keyboard, notification, render

logger = logging.getLogger(__name__)

# The whole claim is the send budget: delivery fetches nothing, so the only work between claiming and
# sending is the match itself.
SEND_BUDGET_SECONDS = CLAIM_TIMEOUT_MIN["notify"] * 60 * 0.6

# What Telegram says when it will never take a message for this chat again: any 403 ("bot was blocked
# by the user", "bot can't initiate conversation with a user") or a chat that no longer exists.
TERMINAL_ERRORS = ("forbidden", "chat not found")


def unlimited_items(gap_seconds: float) -> int:
    """The most one run could send if nothing stopped it, plus one to prove there is more."""
    return int(SEND_BUDGET_SECONDS / max(gap_seconds, 0.1)) + 1


def deliver(db: Database, user_id: _uuid.UUID, *,
            telegram: Callable[[str, str, dict], None] | None,
            push: Callable[[PushSubscription, dict], bool] | None) -> int:
    """Send what this person is owed through every channel they have; how many jobs went.

    `telegram(chat_id, text, keyboard)` and `push(subscription, payload)` are the transports, `None`
    when the notifier holds no key for them. `push` answers False for a browser that has gone.
    """
    user = get(db, User, user_id)
    if user is None:
        return 0
    browsers = find(db, PushSubscription, {"user_id": user_id})
    chat = user.telegram_status == "connected" and bool(user.telegram_chat_id)
    if (chat and telegram is None) or (browsers and push is None):
        logger.warning("notify: %s has a channel this notifier holds no key for — set "
                       "TELEGRAM_BOT_TOKEN and the VAPID keys", user_id)
    if push is None:
        browsers = []
    if not (chat and telegram) and not browsers:
        return 0

    # The operator's ceilings for this account, and `None` means unlimited (`libs.limits`).
    gap, limit = _run_budget(db, user)
    if limit == 0:
        return 0
    # Oldest first: what is owed starts after the newest job sent, so a run stopped part-way leaves
    # exactly the rest owed, and nothing is skipped.
    batch = owed(db, user)[:limit]

    sent = 0
    deadline = monotonic() + SEND_BUDGET_SECONDS
    for match in batch:
        # Stop before the claim does, and go straight back into the queue rather than wait.
        if monotonic() >= deadline:
            _deliver_again(db, user_id)
            logger.info("notify: %s sent %d and ran out of its send budget — continuing "
                        "immediately", user_id, sent)
            return sent
        # Paced: Telegram takes about one message a second into a chat.
        reached = _to_telegram(db, user, match, telegram, pause=gap if sent else 0)
        if not (_to_browsers(db, user_id, browsers, match, push) or reached):
            break
        # Marked the moment it has gone, one job at a time.
        record_state(db, user_id, match.job.id, notified_at=now())
        sent += 1

    # A full batch means the limit bound rather than the supply, so there may be more behind it.
    if sent == len(batch) >= limit:
        _deliver_again(db, user_id)
    if sent:
        moment = now()
        audit(logger, "notify.sent", user=user_id, items=sent, median_wait_min=round(
            statistics.median((moment - m.job.first_seen_at).total_seconds() / 60
                              for m in batch[:sent])))
    return sent


def _to_telegram(db: Database, user: User, match: Match,
                 telegram: Callable[[str, str, dict], None] | None, *, pause: float) -> bool:
    """The job to their Telegram chat, if it still takes messages; whether it went."""
    if telegram is None or user.telegram_status != "connected" or not user.telegram_chat_id:
        return False
    sleep(pause)
    try:
        telegram(user.telegram_chat_id, render(match.job, match.words), keyboard(match.job))
    except Exception as exc:
        if not _is_terminal(exc):
            raise
        _telegram_gone(db, user, exc)
        return False
    return True


def _to_browsers(db: Database, user_id: _uuid.UUID, browsers: list[PushSubscription],
                 match: Match, push: Callable[[PushSubscription, dict], bool] | None) -> bool:
    """The job to every browser still open this run; whether any took it. A gone one is deleted."""
    reached = False
    payload = notification(match.job)
    for browser in list(browsers):
        try:
            if push(browser, payload):
                reached = True
                continue
            # Its push service says it unsubscribed, and it will never accept again.
            db[PushSubscription.COLLECTION].delete_one({"endpoint": browser.endpoint})
        except Exception as exc:  # noqa: BLE001 - one browser failing must not cost the others
            logger.warning("push: a browser of %s refused job %s (%s); skipped for this run",
                           user_id, match.job.id, type(exc).__name__)
        browsers.remove(browser)
    return reached


def _run_budget(db: Database, user: User) -> tuple[float, int]:
    """How fast this run may send, and how many jobs it may send."""
    # Never None: `send_gap_seconds` is Telegram's rule rather than ours and forbids unlimited.
    gap = limits.resolve(user, "send_gap_seconds") or limits.DEFAULT_SEND_GAP_SECONDS
    per_run = limits.resolve(user, "items_per_run")
    per_day = limits.resolve(user, "notifications_per_day")

    if per_day is not None:
        remaining = per_day - sent_today(db, user.id)
        if remaining <= 0:
            logger.info("notify: %s is at their daily cap, deferring", user.id)
            return gap, 0
        # A daily ceiling bounds an unlimited per-run one; two unlimiteds stay unlimited.
        per_run = remaining if per_run is None else min(per_run, remaining)

    return gap, per_run if per_run is not None else unlimited_items(gap)


def _deliver_again(db: Database, user_id: _uuid.UUID) -> None:
    """Queue the rest of this account's alerts now. There is nothing to wait for."""
    # This run's own job still holds the `due` slot, and the dedup index refuses a second one while
    # it does. The worker's later `complete` writes only the state, so it cannot put the key back.
    db[QueueJob.COLLECTION].update_one(
        {"lane": "notify", "user_id": user_id, "state": "claimed", "dedup_key": "due"},
        {"$set": {"dedup_key": None}})
    enqueue(db, "notify", user_id, dedup_key="due")


def _telegram_gone(db: Database, user: User, exc: Exception) -> None:
    """Telegram will not take messages for this chat again: stop trying, and say so on the site."""
    # Here too, so the rest of this run stops trying the chat.
    user.telegram_status = "broken"
    db[User.COLLECTION].update_one({"_id": user.id}, {"$set": {"telegram_status": "broken"}})
    # The one failure Telegram itself cannot tell them about.
    notify(db, user.id, TELEGRAM_BROKEN,
           "Telegram stopped taking our messages, so jobs are not reaching you there. If you "
           "blocked the bot, connect Telegram again under Your details.")
    logger.warning("telegram: %s's chat refused delivery (%s); marked broken", user.id,
                   str(exc)[:200])


def _is_terminal(exc: Exception) -> bool:
    message = str(exc).casefold()
    return any(marker in message for marker in TERMINAL_ERRORS)
