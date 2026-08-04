"""Every ceiling that applies to one account, and who is allowed to change it.

**The operator sets these, not the user.** A person who could raise their own delivery ceiling is a
person who can get their own Telegram bot rate-limited, and the ones below that are not about
delivery are about spend. So they live on the user row, are edited from the admin panel, and appear
nowhere in a user's own settings.

## Absent, `null`, and a number are three different things

- **absent** — follow the platform default, and keep following it as that default changes;
- **`null`** — unlimited, chosen deliberately;
- **a number** — that number.

Storing a very large number to mean unlimited would collapse the first two cases and make a
misconfiguration indistinguishable from an intention. `resolve` returns `None` for unlimited, and
every caller has to decide what that means rather than comparing against a sentinel.

## Why a daily delivery cap is not the interesting one

It was the only guard against Telegram's rate limits, and it is a poor one: it stops a user hearing
about jobs they matched rather than stopping the bot sending too fast. Pacing does that properly
(`services.notifier.send`), which is why the cap can now be `null` while `send_gap_seconds` cannot.
"""
from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from libs.db.models import User


@dataclass(frozen=True)
class Limit:
    """One ceiling: what it is called, what it defaults to, and what it means."""

    key: str
    label: str
    default: int | None
    unit: str
    detail: str
    # Whether an operator may set this to unlimited. `send_gap_seconds` may not — it is Telegram's
    # rule rather than ours, and removing it is how a bot gets restricted.
    allow_unlimited: bool = True


# Telegram's own numbers, from its Bot FAQ: one message per second to a chat, **20 per minute to a
# group or channel**, ~30 per second across chats. The channel limit is the binding one, so four
# seconds (15/min) sits under it with headroom while three would be exactly at the line.
DEFAULT_SEND_GAP_SECONDS = 4

LIMITS: tuple[Limit, ...] = (
    Limit("notifications_per_day", "Jobs delivered per day", None, "jobs",
          "How many matches reach this account in a day. Unlimited is safe now that sending is "
          "paced — the cap used to be the only guard against Telegram's rate limits, and it "
          "guarded them by withholding jobs the user had matched."),
    Limit("items_per_run", "Jobs per delivery run", 10, "jobs",
          "How many are sent in one 15-minute cycle. Anything over stays queued for the next one; "
          "nothing is dropped."),
    Limit("send_gap_seconds", "Seconds between messages", DEFAULT_SEND_GAP_SECONDS, "seconds",
          "Telegram allows 20 messages a minute to a channel and answers 429 above it. Four "
          "seconds is 15 a minute.", allow_unlimited=False),
    Limit("proxy_mb_per_day", "LinkedIn bandwidth per day", 200, "MB",
          "Their own residential proxy pays for this. LinkedIn is the only source that uses it."),
    # **Not unlimited-able.** "Unlimited minutes between runs" has no meaning, and offering it in a
    # dropdown invites somebody to pick the option that stops their own LinkedIn tier. Turning the
    # tier off is `HARVEST_LINKEDIN_DISABLED`, which is a different decision in a different place.
    Limit("linkedin_interval_min", "Minutes between LinkedIn runs", 15, "minutes",
          "How often this account's LinkedIn tier runs. Shorter means fresher and more bandwidth.",
          allow_unlimited=False),
    Limit("describe_per_run", "Job descriptions fetched per run", 60, "postings",
          "Each one is a request through their proxy. The rest wait for the next cycle."),
    Limit("keywords", "Search terms", 25, "terms",
          "More terms means broader collection and more proxy spend."),
    Limit("documents", "Uploaded documents", 25, "files",
          "CVs, references, diplomas — what the setup agent reads from."),
    Limit("conversations", "Setup chats", 3, "chats",
          "Every turn replays its whole chat on the user's own credential, so more chats is more "
          "spend re-reading them."),
    Limit("label_pool", "Jobs bought per labelling run", 300, "jobs",
          "Labelling runs on the user's own Claude credential, so this is their money."),
)

BY_KEY = {limit.key: limit for limit in LIMITS}


def resolve(user: User, key: str) -> int | None:
    """This account's ceiling, or `None` for unlimited.

    `None` is a real answer and not an error — every caller has to handle "no ceiling" explicitly
    rather than comparing against a magic number that could also be a genuine setting.
    """
    if key not in BY_KEY:
        raise KeyError(f"unknown limit {key!r}; known: {sorted(BY_KEY)}")
    stored = user.limits or {}
    if key not in stored:
        return BY_KEY[key].default
    value = stored[key]
    return None if value is None else int(value)


def all_for(user: User) -> dict[str, int | None]:
    """Every ceiling this account is running under. What the admin panel renders."""
    return {limit.key: resolve(user, limit.key) for limit in LIMITS}


def set_limit(session: Session, user_id: _uuid.UUID, key: str, value: int | None) -> None:
    """Set or clear one ceiling. Does not commit — the caller owns the transaction."""
    limit = BY_KEY.get(key)
    if limit is None:
        raise KeyError(f"unknown limit {key!r}; known: {sorted(BY_KEY)}")
    if value is None and not limit.allow_unlimited:
        raise ValueError(f"{key} cannot be unlimited: {limit.detail}")
    if value is not None and value < 1:
        raise ValueError(f"{key} must be at least 1, or unlimited")

    user = session.get(User, user_id)
    if user is None:
        raise KeyError(f"no such user: {user_id}")
    # Replaced rather than mutated: SQLAlchemy does not track in-place changes to a JSONB dict, and
    # a mutation that never reaches the database is the worst kind of setting.
    user.limits = {**(user.limits or {}), key: value}


def describe() -> list[dict]:
    """The catalogue, for the admin panel to render without hardcoding any of it."""
    return [{"key": limit.key, "label": limit.label, "default": limit.default,
             "unit": limit.unit, "detail": limit.detail,
             "allow_unlimited": limit.allow_unlimited} for limit in LIMITS]
