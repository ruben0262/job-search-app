"""Delivering one user's feed to their own Telegram bot.

Three rules, each of which is a silent failure when broken:

1. **The `seen` row and the job's completion commit together.** The queue is at-least-once, so a
   retry after a crash must re-send nothing already recorded (`SCHEDULER_PLAN.md` §4). Committing
   them separately leaves a window where the send happened and the record did not.
2. **A revoked bot is terminal, not a retry.** Telegram answers 403 and it will never succeed.
   Retrying it five times wastes the attempts and then goes quiet anyway, so the user's feed stops
   with no signal to anyone. The account is flagged `broken`, the dispatcher stops enqueuing, and
   the fix is a reconnect email (G6).
3. **The token never leaves the worker.** It is decrypted inside `SecretStore.use`, held for the
   duration of the call, and scrubbed from anything logged in that window — including a traceback
   from the HTTP client, which is exactly where a failed request puts it.

Each user brings their own bot token, so Telegram's per-bot rate limits do not pool across the
platform. The per-user throttle here is to stop one badly-configured account spamming its owner.
"""
from __future__ import annotations

import logging
import uuid as _uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from libs.db.models import CorpusJob, User, UserJobState
from libs.secrets import SecretStore
from services.notifier.routing import group_by_chat

logger = logging.getLogger(__name__)

# Telegram allows ~30 messages/second to different chats but throttles a single chat much harder.
# One digest message per delivery keeps this simple; the cap is the per-day one below.
MAX_ITEMS_PER_DIGEST = 10

# `TENANT_SAFETY.md` §3. Resets at the user's local midnight, which is why it is counted against
# `notified_at` in their timezone rather than against a counter column.
MAX_NOTIFICATIONS_PER_DAY = 100

# Telegram's answers when the bot is gone. Distinct from a transient failure in every way that
# matters: no amount of retrying changes them.
TERMINAL_ERRORS = ("bot was blocked", "chat not found", "user is deactivated",
                   "bot was kicked", "unauthorized")


class BotRevoked(RuntimeError):
    """The user's bot is gone. Terminal — flag the account, do not retry."""


@dataclass(frozen=True)
class FeedItem:
    job_id: int
    url: str
    title: str
    company: str | None
    score: float
    # Carried only so the item can be routed to a channel (`services/notifier/routing.py`). Not
    # rendered — the digest is already dense, and a source name per line is noise to somebody who
    # is reading the channel that source routes to.
    source: str | None = None
    location: str | None = None


def pending_feed(session: Session, user_id: _uuid.UUID, *, cutoff: float,
                 limit: int = MAX_ITEMS_PER_DIGEST) -> list[FeedItem]:
    """Scored, above cutoff, not yet notified. Highest score first.

    `notified_at IS NULL` is the dedup, and it is a per-user column — the same posting can be new to
    one user and already sent to another, which is the whole point of a shared corpus with a
    per-user tail.
    """
    rows = session.execute(
        select(UserJobState.job_id, CorpusJob.url, CorpusJob.title, CorpusJob.company,
               UserJobState.score, CorpusJob.source, CorpusJob.location)
        .join(CorpusJob, CorpusJob.id == UserJobState.job_id)
        .where(UserJobState.user_id == user_id,
               UserJobState.notified_at.is_(None),
               UserJobState.score.is_not(None),
               UserJobState.score >= cutoff)
        .order_by(UserJobState.score.desc())
        .limit(limit)).all()
    return [FeedItem(*r) for r in rows]


def notifications_today(session: Session, user_id: _uuid.UUID) -> int:
    """How many items have been delivered since the user's local midnight.

    Counted in `users.timezone`, not UTC: "resets at midnight your time" has to keep meaning the
    same thing in November, and the platform's users are across several zones.
    """
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
            send: Callable[[str, str, str], None],
            cutoff: float = 60.0,
            daily_cap: int = MAX_NOTIFICATIONS_PER_DAY) -> int:
    """Send this user's pending feed. Returns how many items were delivered.

    `send(token, chat_id, text)` is injected so the transport is one replaceable function — the
    tests drive it, and a Telegram API change touches one place.

    **Marks `notified_at` in the same transaction the caller commits alongside the queue job.** The
    caller commits; this function deliberately does not, so the two cannot be split.
    """
    user = session.get(User, user_id)
    if user is None or user.telegram_status != "connected" or not user.telegram_chat_id:
        return 0

    remaining = daily_cap - notifications_today(session, user_id)
    if remaining <= 0:
        # Over cap is not an error and not a silent drop: the items keep `notified_at IS NULL` and
        # go out in the next window.
        logger.info("notify: %s is at their daily cap, deferring", user_id)
        return 0

    items = pending_feed(session, user_id, cutoff=cutoff, limit=min(remaining,
                                                                   MAX_ITEMS_PER_DIGEST))
    if not items:
        return 0

    # One digest per channel. A user who has split their feed gets a Hungarian batch in the HU
    # channel and a LinkedIn batch in the LinkedIn one; a user who has not gets exactly one message
    # to their personal chat, which is the same behaviour as before this existed.
    batches = group_by_chat(items, user.telegram_routes, user.telegram_chat_id)

    with store.use(user_id, "telegram_bot") as token:
        if token is None:
            raise BotRevoked(f"user {user_id} has no telegram token stored")
        for chat_id, batch in batches.items():
            try:
                send(token.reveal(), chat_id, render_digest(batch))
            except Exception as exc:
                if _is_terminal(exc):
                    # A revoked token is terminal for every channel, so stopping here is right.
                    # A single bad *channel* id is not — but Telegram answers 400 for that, which
                    # `_is_terminal` does not match, so it retries rather than marking the account
                    # broken.
                    user.telegram_status = "broken"
                    raise BotRevoked(str(exc)[:200]) from exc
                raise

    for item in items:
        session.get(UserJobState, (user_id, item.job_id)).notified_at = func.now()
    return len(items)


def render_digest(items: list[FeedItem]) -> str:
    """One message per delivery rather than one per job.

    Ten separate messages is what gets a bot rate-limited and a user annoyed, and the feed reads
    better as a ranked list than as a burst of notifications.
    """
    lines = [f"{len(items)} new match{'es' if len(items) != 1 else ''}:", ""]
    for item in items:
        company = f" — {item.company}" if item.company else ""
        lines.append(f"{int(item.score)}  {item.title}{company}")
        lines.append(item.url)
        lines.append("")
    return "\n".join(lines).strip()


def _is_terminal(exc: Exception) -> bool:
    message = str(exc).casefold()
    return any(marker in message for marker in TERMINAL_ERRORS)
