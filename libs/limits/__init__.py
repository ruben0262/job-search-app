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
    # **Unlimited by default, on the owner's instruction: nothing should stand between a scored
    # match and Telegram.** Filtering and scoring already decided what is worth sending, so a
    # further ceiling here does not protect anything — it only delays a posting the platform has
    # already judged, and the product's whole claim is being early to it.
    #
    # It was 10, and 10 × 96 cycles is 960 a day: measured 2026-08-06 at 931 delivered and 576
    # matched postings that expired unsent. The cap was the largest single loss of work on the
    # platform. Telegram's own pacing (`send_gap_seconds`) is the only limit left, and that one is
    # theirs rather than ours.
    Limit("items_per_run", "Jobs per delivery run", None, "jobs",
          "How many are sent in one cycle. Unlimited means a run keeps sending until the queue is "
          "empty or its claim runs out, and whatever is left is picked up immediately rather than "
          "at the next cycle."),
    Limit("send_gap_seconds", "Seconds between messages", DEFAULT_SEND_GAP_SECONDS, "seconds",
          "Telegram allows 20 messages a minute to a channel and answers 429 above it. Four "
          "seconds is 15 a minute.", allow_unlimited=False),
    Limit("proxy_mb_per_day", "LinkedIn bandwidth per day", 200, "MB",
          "Their own residential proxy pays for this. LinkedIn is the only source that uses it."),
    # **The single biggest filter on a feed, and it was a constant nobody could see.** Measured
    # 2026-08-05: 87 of one account's 90 fresh matches were dropped by it, leaving three. LinkedIn
    # stops counting at 200 and displays "Over 200 applicants", so every popular posting arrives
    # pinned at exactly the cap — and an account whose model likes mainstream postings has its whole
    # feed sitting on that number. Per user rather than platform-wide because it is a judgement
    # about one person's odds, not a platform safety rule (ROADMAP 6ag).
    #
    # Unlimited means "show me everything regardless of how many have applied", which is a real
    # position: a strong candidate applying to a 300-applicant role is not wasting their time.
    Limit("applicant_cap", "Drop jobs at or above this many applicants", 200, "applicants",
          "LinkedIn only — it is the one source reporting a count, and it stops counting at 200, "
          "so a posting pinned there has 200 or more. Unlimited shows every job whatever the "
          "count."),
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
    # **Not unlimited-able**, for the same reason `send_gap_seconds` is not: the ceiling protects
    # somebody rather than expressing a preference. Labelling spends the user's own Claude
    # credential, "unlimited" is not a bigger target but an unbounded bill, and the option was being
    # read as 300 anyway — so it was a choice that looked available and was not.
    # **Moved here from the plan-quota system on 2026-08-05**, which was a second per-user limits
    # mechanism with two keys — `notifications_per_day` and `proxy_mb_per_day` — already duplicated
    # in this catalogue. It enforced exactly this one limit and nothing wrote its override column,
    # so a support exception was impossible to grant (ROADMAP 6as).
    #
    # Not unlimited-able: a build spends the user's own Claude credential, so the ceiling protects
    # their money rather than expressing a preference — the same reason `label_pool` cannot be.
    Limit("cv_builds_per_day", "CV and cover letters per day", 10, "documents",
          "Each one is a Claude call on this account's own credential.", allow_unlimited=False),
    Limit("label_pool", "Jobs bought per labelling run", 300, "jobs",
          "Labelling runs on the user's own Claude credential, so this is their money.",
          allow_unlimited=False),
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


def limit_default(key: str) -> int | None:
    """The catalogue's default for a limit, for a caller with no user to resolve against.

    Exists so a module needing a sensible fallback writes `limit_default("applicant_cap")` rather
    than its own copy of the number. A constant beside a catalogue entry naming the same thing is
    the defect this project has shipped most often.
    """
    if key not in BY_KEY:
        raise KeyError(f"unknown limit {key!r}; known: {sorted(BY_KEY)}")
    return BY_KEY[key].default


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
