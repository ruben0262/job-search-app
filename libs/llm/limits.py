"""Where a plan's limits stand: its 5-hour window and its weekly one (owner, 2026-11-14), for a person who
signed in to their own Claude account, or to their ChatGPT one (owner, 2026-11-19). What Claude Code's own
/usage and Codex's own /status show.

Asked of Anthropic, or of ChatGPT, with the access token the sign-in already holds. No tool is run, so
nothing here can renew a sign-in. A renewal that is not saved retires the stored sign-in: a throwaway probe
did exactly that on 2026-11-14 and the account could write nothing until its owner signed in again. One
place renews a sign-in and saves what it left, `libs.llm.cli.CliClient`, and this is not a second. A token
that has run out is left alone: the limits show again after the person's next document, which renews it.

An API key has no plan windows: what each document used is said with the document instead
(`BuildStatus.tokens`). A Google account's are not read: Gemini CLI has served no Google sign-in since
2026-06-18, so a Gemini sign-in writes nothing to count.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

import httpx
from pymongo.database import Database

from libs.db.models import QueueJob, UserSecret
from libs.llm.cli import _jwt_claims
from libs.secrets import redacting

logger = logging.getLogger(__name__)

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
# The windows shown, in this order, and what each is called.
PLAN_WINDOWS = (("five_hour", "5-hour limit"), ("seven_day", "Weekly limit"))
# What Codex 0.167 asks for its /status (`wham/usage` in its binary): the same two windows, each with its
# length in seconds, which names it (2026-11-19, a Plus plan: 18,000 and 604,800).
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
CODEX_WINDOWS = (("primary_window", "five_hour"), ("secondary_window", "seven_day"))
# The sign-ins whose plan's limits can be read: Settings shows them under these alone
# (`AiAccount.plan_limits`).
LIMITED_KINDS = ("claude_login", "codex_login")
# A Settings page waits this long for the limits and no longer.
LIMITS_TIMEOUT_SEC = 8
# For how long what a provider said is what a page is told (`held`). The site reads a plan's limits ahead on
# every page it loads, so that Settings shows them at once (owner, 2026-11-19), and every one of those was a
# call to Anthropic or to ChatGPT with the person's own token, holding a worker for up to the 8 s above. A
# plan's windows are five hours and a week long: five minutes late is not wrong, and a document made here,
# which is what moves them from here, ends the wait at once.
LIMITS_KEEP = timedelta(minutes=5)


@dataclass(frozen=True)
class Window:
    """One limit of a plan: the share of it used, 0 to 100, and when it starts again."""

    id: str
    label: str
    percent: float
    resets_at: datetime | None


def read(kind: str, blob: str, *, http: httpx.Client | None = None,
         now: datetime | None = None) -> list[Window]:
    """The plan's windows for a Claude or ChatGPT sign-in. None of them when they cannot be read now:
    another kind of account, a token that ran out, a refusal, or an answer that names no window we know."""
    if kind not in LIMITED_KINDS:
        return []
    try:
        if kind == "claude_login":
            login = json.loads(json.loads(blob)[".credentials.json"])["claudeAiOauth"]
            token, expires, account = str(login["accessToken"]), login.get("expiresAt"), None
        else:
            tokens = json.loads(json.loads(blob)["auth.json"])["tokens"]
            token, account = str(tokens["access_token"]), str(tokens["account_id"])
            # Its expiry is in the token itself, in seconds; Claude's beside it, in milliseconds.
            expires = _jwt_claims(token).get("exp")
            expires = expires * 1000 if isinstance(expires, int | float) else None
    except (ValueError, KeyError, TypeError):
        logger.warning("limits: a %s sign-in holds no access token; its limits are not shown", kind)
        return []
    if isinstance(expires, int | float) and expires / 1000 <= (now or datetime.now(UTC)).timestamp():
        # Run out, which is how a sign-in not used for some hours stands. Not a fault, and not renewed here.
        return []
    who = "Anthropic" if kind == "claude_login" else "ChatGPT"
    with redacting(token):
        try:
            answer = (http or httpx).get(
                USAGE_URL if kind == "claude_login" else CODEX_USAGE_URL, timeout=LIMITS_TIMEOUT_SEC,
                headers={"Authorization": f"Bearer {token}",
                         **({"anthropic-beta": "oauth-2025-04-20"} if account is None
                            else {"ChatGPT-Account-Id": account})})
        except httpx.HTTPError as exc:
            logger.warning("limits: %s could not be reached (%s); the limits are not shown", who,
                           type(exc).__name__)
            return []
    if answer.status_code != 200:
        logger.warning("limits: %s answered %d; the limits are not shown", who, answer.status_code)
        return []
    try:
        said = answer.json()
    except ValueError:
        said = None
    said = said if isinstance(said, dict) else {}
    found = _claude(said) if kind == "claude_login" else _codex(said)
    if not found:
        logger.warning("limits: %s's answer named no window we know; the limits are not shown", who)
    return found


def _claude(said: dict) -> list[Window]:
    return [Window(id_, label, float(window["utilization"]), _when(window.get("resets_at")))
            for id_, label in PLAN_WINDOWS
            if isinstance(window := said.get(id_), dict)
            and isinstance(window.get("utilization"), int | float)]


def _codex(said: dict) -> list[Window]:
    """ChatGPT's two windows, named as Claude's are. A window of another length is passed over rather than
    called by the wrong name."""
    limit = said.get("rate_limit") if isinstance(said.get("rate_limit"), dict) else {}
    lengths = {"five_hour": 5 * 3600, "seven_day": 7 * 86400}
    found = []
    for field, id_ in CODEX_WINDOWS:
        window = limit.get(field)
        if (isinstance(window, dict) and isinstance(window.get("used_percent"), int | float)
                and window.get("limit_window_seconds") == lengths[id_]):
            resets = window.get("reset_at")
            when = datetime.fromtimestamp(resets, UTC) if isinstance(resets, int | float) else None
            found.append(Window(id_, dict(PLAN_WINDOWS)[id_], float(window["used_percent"]), when))
    return found


def _when(said: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(said).replace("Z", "+00:00")) if said else None
    except ValueError:
        return None


def held(db: Database, user_id, account_id, *, moment: datetime | None = None) -> list[Window] | None:
    """What this account's limits were last read as, while that still stands: read within `LIMITS_KEEP`, and
    no document made for its person since. None otherwise, and for an account that is not theirs: the
    provider is to be asked."""
    row = db[UserSecret.COLLECTION].find_one({"_id": account_id, "user_id": user_id},
                                             {"limits": 1, "limits_at": 1})
    read_at = row and row.get("limits_at")
    if not read_at or read_at < (moment or datetime.now(UTC)) - LIMITS_KEEP:
        return None
    if db[QueueJob.COLLECTION].count_documents(
            {"lane": "docgen", "user_id": user_id, "finished_at": {"$gte": read_at}}, limit=1):
        return None
    return [Window(**each) for each in row["limits"]]


def hold(db: Database, user_id, account_id, windows: list[Window]) -> None:
    """Keep what the provider just said. Not an answer with no window in it: that is a sign-in not used
    for some hours, which is not asked at all, or a provider that did not answer, which is asked again."""
    if windows:
        db[UserSecret.COLLECTION].update_one(
            {"_id": account_id, "user_id": user_id},
            {"$set": {"limits": [asdict(each) for each in windows], "limits_at": datetime.now(UTC)}})
