"""Where a Claude plan's limits stand: its 5-hour window and its weekly one (owner, 2026-11-14), for a
person who signed in to their own Claude account. What Claude Code's own /usage shows.

Asked of Anthropic with the access token the sign-in already holds. No tool is run, so nothing here can
renew a sign-in. A renewal that is not saved retires the stored sign-in: a throwaway probe did exactly
that on 2026-11-14 and the account could write nothing until its owner signed in again. One place renews
a sign-in and saves what it left, `libs.llm.cli.CliClient`, and this is not a second. A token that has run
out is left alone: the limits show again after the person's next document, which renews it.

An API key has no plan windows. A ChatGPT plan's and a Google account's are not read (ROADMAP item 50).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from libs.secrets import redacting

logger = logging.getLogger(__name__)

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
# The windows shown, in this order, and what each is called.
PLAN_WINDOWS = (("five_hour", "5-hour limit"), ("seven_day", "Weekly limit"))
# A Settings page waits this long for the limits and no longer.
LIMITS_TIMEOUT_SEC = 8


@dataclass(frozen=True)
class Window:
    """One limit of a plan: the share of it used, 0 to 100, and when it starts again."""

    id: str
    label: str
    percent: float
    resets_at: datetime | None


def read(kind: str, blob: str, *, http: httpx.Client | None = None,
         now: datetime | None = None) -> list[Window]:
    """The plan's windows for a Claude sign-in. None of them when they cannot be read now: another kind
    of account, a token that ran out, a refusal, or an answer that names no window we know."""
    if kind != "claude_login":
        return []
    try:
        login = json.loads(json.loads(blob)[".credentials.json"])["claudeAiOauth"]
        token, expires = str(login["accessToken"]), login.get("expiresAt")
    except (ValueError, KeyError, TypeError):
        logger.warning("limits: a Claude sign-in holds no access token; its limits are not shown")
        return []
    if isinstance(expires, int | float) and expires / 1000 <= (now or datetime.now(UTC)).timestamp():
        # Run out, which is how a sign-in not used for some hours stands. Not a fault, and not renewed here.
        return []
    with redacting(token):
        try:
            answer = (http or httpx).get(USAGE_URL, timeout=LIMITS_TIMEOUT_SEC, headers={
                "Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20"})
        except httpx.HTTPError as exc:
            logger.warning("limits: Anthropic could not be reached (%s); the limits are not shown",
                           type(exc).__name__)
            return []
    if answer.status_code != 200:
        logger.warning("limits: Anthropic answered %d; the limits are not shown", answer.status_code)
        return []
    try:
        said = answer.json()
    except ValueError:
        said = None
    found = [Window(id_, label, float(window["utilization"]), _when(window.get("resets_at")))
             for id_, label in PLAN_WINDOWS
             if isinstance(window := (said.get(id_) if isinstance(said, dict) else None), dict)
             and isinstance(window.get("utilization"), int | float)]
    if not found:
        logger.warning("limits: Anthropic's answer named no window we know; the limits are not shown")
    return found


def _when(said: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(said).replace("Z", "+00:00")) if said else None
    except ValueError:
        return None
