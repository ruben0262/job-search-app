"""Every ceiling that applies to one account, and who is allowed to change it."""
from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass

from sqlalchemy import func, select
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
    # Whether an operator may set this to unlimited.
    allow_unlimited: bool = True
    # The values the admin panel offers. Here rather than in the panel, because a ceiling with no
    # settable values is a ceiling nobody can change: the options used to live in a `CHOICES` map
    # in `AdminUserPage.tsx`, so adding a limit here produced a row with an empty dropdown and no
    # error anywhere. One definition, served through `describe()`, with nothing to drift.
    choices: tuple[int, ...] = ()
    # The smallest value an operator may set. 0 only where 0 means something: none of it.
    minimum: int = 1


# Telegram's Bot FAQ: 1/second to a chat, 20/minute to a channel, ~30/second across chats.
DEFAULT_SEND_GAP_SECONDS = 4

LIMITS: tuple[Limit, ...] = (
    Limit("notifications_per_day", "Jobs delivered per day", None, "jobs",
          "How many matches reach this account in a day. Unlimited is safe now that sending is "
          "paced — the cap used to be the only guard against Telegram's rate limits, and it "
          "guarded them by withholding jobs the user had matched.",
          choices=(25, 50, 100, 250, 500, 1000)),
    # Unlimited by default: nothing should stand between a scored match and Telegram.
    Limit("items_per_run", "Jobs per delivery run", None, "jobs",
          "How many are sent in one cycle. Unlimited means a run keeps sending until the queue is "
          "empty or its claim runs out, and whatever is left is picked up immediately rather than "
          "at the next cycle.",
          choices=(5, 10, 15, 20, 30, 50)),
    Limit("send_gap_seconds", "Seconds between messages", DEFAULT_SEND_GAP_SECONDS, "seconds",
          "Telegram allows 20 messages a minute to a channel and answers 429 above it. Four "
          "seconds is 15 a minute.", allow_unlimited=False,
          choices=(3, 4, 5, 8, 12, 20)),
    Limit("proxy_mb_per_day", "LinkedIn bandwidth per day", 200, "MB",
          "Their own residential proxy pays for this. LinkedIn is the only source that uses it.",
          choices=(100, 200, 500, 1000, 2000)),
    # The single biggest filter on a feed, and it was a constant nobody could see.
    Limit("applicant_cap", "Drop jobs at or above this many applicants", 200, "applicants",
          "LinkedIn only — it is the one source reporting a count, and it stops counting at 200, "
          "so a posting pinned there has 200 or more. Unlimited shows every job whatever the "
          "count.",
          choices=(10, 25, 50, 100, 150, 200)),
    # Not unlimited-able.
    Limit("linkedin_interval_min", "Minutes between LinkedIn runs", 15, "minutes",
          "How often this account's LinkedIn tier runs. Shorter means fresher and more bandwidth.",
          allow_unlimited=False,
          choices=(15, 30, 60, 120)),
    Limit("describe_per_run", "Job descriptions fetched per run", 60, "postings",
          "Each one is a request through their proxy. The rest wait for the next cycle.",
          choices=(20, 40, 60, 100, 200)),
    Limit("keywords", "Search terms", 25, "terms",
          "More terms means broader collection and more proxy spend.",
          choices=(10, 25, 50, 100)),
    Limit("documents", "Uploaded documents", 25, "files",
          "CVs, references, diplomas — what the setup agent reads from.",
          choices=(10, 25, 50, 100)),
    Limit("conversations", "Setup chats", 3, "chats",
          "Every turn replays its whole chat on the user's own credential, so more chats is more "
          "spend re-reading them.",
          choices=(1, 3, 5, 10)),
    Limit("label_pool", "Jobs bought per labelling run", 300, "jobs",
          "Labelling runs on the user's own Claude credential, so this is their money.",
          allow_unlimited=False,
          # 1,500 and 2,000 added 2026-08-14. This is not "per run" in practice: cold start stops
          # for good once an account reaches its target and nothing tops it up as the corpus grows,
          # so this number is the entire automatic training set the account will ever have. The
          # first account to be labelled by the platform rather than migrated stopped at 338.
          choices=(100, 300, 500, 1000, 1500, 2000)),
    # Every CV and cover letter asked for, instant or AI, counted at the one place they are asked
    # for (`POST /documents/build`). Generous, because an instant one is a render: fifty is
    # twenty-five applications a day, and still a ceiling on a script or a loop. Never unlimited.
    Limit("cv_builds_per_day", "CV and cover letters per day", 50, "documents",
          "Instant ones cost a render. AI ones are a model call on this account's own key, or on "
          "the platform's within the allowance below.", allow_unlimited=False,
          choices=(10, 20, 50, 100, 200)),
    # Never unlimited: every one is the platform's money. A small allowance for everybody (owner,
    # 2026-10-28), spent only when a platform key is configured (`libs.llm.providers`).
    Limit("ai_platform_requests_per_day", "AI requests on the platform's key per day", 20,
          "requests",
          "AI-written documents and support-chat replies for an account with no key of its own, "
          "on the platform's paid key. An account with its own Claude, Gemini or OpenAI key is "
          "not counted. 0 turns it off; instant documents are free either way.",
          allow_unlimited=False, minimum=0, choices=(0, 5, 10, 20, 50)),
)

BY_KEY = {limit.key: limit for limit in LIMITS}


def resolve(user: User, key: str) -> int | None:
    """This account's ceiling, or `None` for unlimited."""
    if key not in BY_KEY:
        raise KeyError(f"unknown limit {key!r}; known: {sorted(BY_KEY)}")
    stored = user.limits or {}
    if key not in stored:
        return BY_KEY[key].default
    value = stored[key]
    return None if value is None else int(value)


def limit_default(key: str) -> int | None:
    """The catalogue's default for a limit, for a caller with no user to resolve against."""
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
    if value is not None and value < limit.minimum:
        raise ValueError(f"{key} must be at least {limit.minimum}"
                         + (", or unlimited" if limit.allow_unlimited else ""))

    user = session.get(User, user_id)
    if user is None:
        raise KeyError(f"no such user: {user_id}")
    # Replaced rather than mutated: SQLAlchemy does not track in-place changes to a JSONB dict.
    user.limits = {**(user.limits or {}), key: value}


def since_their_midnight(session: Session, user_id: _uuid.UUID, column):
    """`column` falls on this person's today, in their own timezone: the one boundary every daily
    ceiling here counts from, so they all reset at the same moment."""
    timezone = session.scalar(select(User.timezone).where(User.id == user_id)) or "UTC"
    return (func.timezone(timezone, column)
            >= func.date_trunc("day", func.timezone(timezone, func.now())))


def describe() -> list[dict]:
    """The catalogue, for the admin panel to render without hardcoding any of it."""
    return [{"key": limit.key, "label": limit.label, "default": limit.default,
             "unit": limit.unit, "detail": limit.detail,
             "allow_unlimited": limit.allow_unlimited,
             "choices": list(limit.choices)} for limit in LIMITS]
