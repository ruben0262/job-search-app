"""Sending a person what their alerts caught: to the bell, their Telegram chat, and every browser they
subscribed.

What is owed is `libs.notify.owed` — what `libs.alerts.route` caught and nothing has sent — so this keeps no
list of its own. Each job goes once, however many alerts caught it, wherever any of them sends, headed by
their names.
"""
from __future__ import annotations

import logging
import statistics
import uuid as _uuid
from collections.abc import Callable
from time import monotonic, sleep

from pymongo.database import Database

from libs import limits
from libs.corpus.texts import with_texts
from libs.db import find, get, now
from libs.db.models import Alert, CorpusJob, PushSubscription, QueueJob, User
from libs.db.models.queue import CLAIM_TIMEOUT_MIN
from libs.db.models.tenant import record_state
from libs.matching import green_words
from libs.notify import TELEGRAM_BROKEN, Event, notify, owed, sent_today
from libs.obs import audit
from libs.queue import enqueue
from libs.tenant.repository import TenantRepository
from services.notifier.card import keyboard, notification, render

logger = logging.getLogger(__name__)

# The whole claim is the send budget: delivery fetches nothing, so the only work between claiming and
# sending is reading what is owed.
SEND_BUDGET_SECONDS = CLAIM_TIMEOUT_MIN["notify"] * 60 * 0.6

# What Telegram says when it will never take a message for this chat again: any 403 ("bot was blocked
# by the user", "bot can't initiate conversation with a user") or a chat that no longer exists.
TERMINAL_ERRORS = ("forbidden", "chat not found")


def unlimited_items(gap_seconds: float) -> int:
    """The most one run could send if nothing stopped it, plus one to prove there is more."""
    return int(SEND_BUDGET_SECONDS / max(gap_seconds, 0.1)) + 1


def deliver(db: Database, user_id: _uuid.UUID, *,
            telegram: Callable[[str, str, dict], None] | None,
            push: Callable[[PushSubscription, dict], bool]) -> int:
    """Send what this person is owed wherever the alerts that caught it send; how many jobs went.

    `telegram(chat_id, text, keyboard)` and `push(subscription, payload)` are the transports, `telegram`
    `None` when no bot token is stored. `push` answers False for a browser that has gone.
    """
    user = get(db, User, user_id)
    if user is None:
        return 0
    browsers = find(db, PushSubscription, {"user_id": user_id})
    chat = user.telegram_status == "connected" and bool(user.telegram_chat_id)
    if chat and telegram is None:
        logger.warning("notify: %s has Telegram connected with no bot token stored, so nothing "
                       "can send to it", user_id)

    # The operator's ceilings for this account, and `None` means unlimited (`libs.limits`).
    gap, limit = _run_budget(db, user)
    if limit == 0:
        return 0
    # Oldest first, so a run stopped part-way leaves exactly the rest owed, and nothing is skipped.
    batch = owed(db, user_id)[:limit]
    if not batch:
        return 0
    jobs = {job.id: job for job in with_texts(db, find(db, CorpusJob,
                                                         {"_id": {"$in": [row["job_id"] for row in batch]}}))}
    alerts = {alert.id: alert for alert in find(db, Alert, {"user_id": user_id})}
    mine = set(green_words(TenantRepository(db).get_config(user_id)))

    sent, chatted = 0, 0
    deadline = monotonic() + SEND_BUDGET_SECONDS
    for row in batch:
        # Stop before the claim does, and go straight back into the queue rather than wait.
        if monotonic() >= deadline:
            _deliver_again(db, user_id)
            logger.info("notify: %s sent %d and ran out of its send budget — continuing "
                        "immediately", user_id, sent)
            return sent
        job = jobs.get(row["job_id"])
        # An alert deleted since it caught the job sends nothing more.
        theirs = [alerts[each] for each in row.get("alerts") or () if each in alerts]
        if job is not None and theirs and _send(db, user, job, theirs, mine, browsers=browsers,
                                                telegram=telegram, push=push, pause=gap if chatted else 0,
                                                again=row.get("pushed_at") is not None):
            chatted += 1
        # Marked the moment it has gone, one job at a time.
        record_state(db, user_id, row["job_id"], notified_at=now())
        sent += 1

    # A full batch means the limit bound rather than the supply, so there may be more behind it.
    if sent == len(batch) >= limit:
        _deliver_again(db, user_id)
    moment = now()
    waits = [(moment - jobs[row["job_id"]].first_seen_at).total_seconds() / 60
             for row in batch[:sent] if row["job_id"] in jobs]
    audit(logger, "notify.sent", user=user_id, items=sent, telegram=chatted,
          median_wait_min=round(statistics.median(waits)) if waits else None)
    return sent


def _send(db: Database, user: User, job: CorpusJob, alerts: list[Alert], mine: set[str], *,
          browsers: list[PushSubscription], telegram: Callable[[str, str, dict], None] | None,
          push: Callable[[PushSubscription, dict], bool], pause: float, again: bool = False) -> bool:
    """One job wherever any of the alerts that caught it sends, headed by their names; whether it went to
    Telegram, which paces the next. `again` is a job whose bell and phones had it on an earlier try, which
    Telegram failed: only Telegram is tried again. Each try sent all of it, so a bad minute at Telegram
    pushed one job to a phone up to five times (2026-11-20)."""
    ways = {way for alert in alerts for way in alert.channels}
    names = [alert.name for alert in alerts]
    if "push" in ways and not again:
        _to_browsers(db, user.id, browsers, job, names=names, push=push)
    for alert in () if again else alerts:
        if "bell" in alert.channels:
            # One line an alert in the bell, brought up to date while it is unread rather than one a job.
            notify(db, user.id, Event(f"alert.{alert.id}", "info", alert.name, f"/alerts?alert={alert.id}"),
                   " · ".join(filter(None, (job.title, job.company))))
    if "telegram" in ways and not again and ways & {"push", "bell"}:
        record_state(db, user.id, job.id, pushed_at=now())
    # Paced: Telegram takes about one message a second into a chat.
    return "telegram" in ways and _to_telegram(db, user, job, names=names, words=_words(job, alerts, mine),
                                               telegram=telegram, pause=pause)


def _words(job: CorpusJob, alerts: list[Alert], mine: set[str]) -> list[str]:
    """Why it was caught: the alerts' words it has, and My search's for an alert that follows it."""
    wanted = {word for alert in alerts for word in alert.keywords}
    if any(alert.search for alert in alerts):
        wanted |= mine
    return sorted(wanted.intersection(job.terms))


def _to_telegram(db: Database, user: User, job: CorpusJob, *, names: list[str], words: list[str],
                 telegram: Callable[[str, str, dict], None] | None, pause: float) -> bool:
    """The job to their Telegram chat, if it still takes messages; whether it went."""
    if telegram is None or user.telegram_status != "connected" or not user.telegram_chat_id:
        return False
    sleep(pause)
    try:
        telegram(user.telegram_chat_id, render(job, words, names), keyboard(job))
    except Exception as exc:
        if not _is_terminal(exc):
            raise
        _telegram_gone(db, user, exc)
        return False
    return True


def _to_browsers(db: Database, user_id: _uuid.UUID, browsers: list[PushSubscription], job: CorpusJob, *,
                 names: list[str], push: Callable[[PushSubscription, dict], bool]) -> bool:
    """The job to every browser still open this run; whether any took it. A gone one is deleted."""
    reached = False
    payload = notification(job, names)
    for browser in list(browsers):
        try:
            if push(browser, payload):
                reached = True
                continue
            # Its push service says it unsubscribed, and it will never accept again.
            db[PushSubscription.COLLECTION].delete_one({"endpoint": browser.endpoint})
        except Exception as exc:  # noqa: BLE001 - one browser failing must not cost the others
            logger.warning("push: a browser of %s refused job %s (%s); skipped for this run",
                           user_id, job.id, type(exc).__name__)
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
           "blocked the bot, connect Telegram again on the Alerts page.")
    logger.warning("telegram: %s's chat refused delivery (%s); marked broken", user.id,
                   str(exc)[:200])


def _is_terminal(exc: Exception) -> bool:
    message = str(exc).casefold()
    return any(marker in message for marker in TERMINAL_ERRORS)
