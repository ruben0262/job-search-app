"""One cheap call per credential kind, proving it works.

`TENANT_SAFETY.md` §2 requires this: *make a real minimal API call. A bad key must fail at the form,
not silently at 3 a.m.* Each validator answers the same question — would this credential let us do
the one thing we need it for? — and nothing else. No validator performs a real unit of work, so a
failed onboarding never leaves a half-done side effect behind.

Deliberately **not** part of `libs.secrets`. Storage must not depend on a third party being
reachable: a user with a working key should still be able to save it during an outage, and whether
to accept an unvalidated credential is the caller's decision rather than an accident of layering.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from libs.secrets import redact

# An OAuth token authenticates on `Authorization: Bearer` and needs this beta header; an API key
# goes on `x-api-key` and does not. Sending one on the other's channel is the most common way a
# perfectly good credential looks broken.
CLAUDE_OAUTH_BETA = "oauth-2025-04-20"

ANTHROPIC_URL = "https://api.anthropic.com/v1/models"
TELEGRAM_URL = "https://api.telegram.org/bot{token}/{method}"

TIMEOUT = 15.0

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ValidationResult:
    """Whether the credential works, and what to tell the user if it does not.

    `detail` is written for the person at the form, not for a log: "this account has no credit" is
    actionable, "402" is not.
    """

    ok: bool
    detail: str = ""
    # Anything the check happened to learn that the platform needs — the Telegram chat id, for
    # instance, which the user should never have to go and find.
    extra: dict[str, str] | None = None


def validate_credential(kind: str, value: str, *,
                        client: httpx.Client | None = None) -> ValidationResult:
    """Dispatch to the right validator. `client` is injectable so tests need no network."""
    validators = {
        "claude_api": _validate_claude_api,
        "claude_oauth": _validate_claude_oauth,
        "telegram_bot": _validate_telegram,
        "proxy": _validate_proxy,
    }
    if kind not in validators:
        return ValidationResult(False, f"{kind!r} is not a credential this platform stores")

    # `trust_env=False` for the same reason the harvester's client factory sets it: an ambient
    # HTTP_PROXY would silently route the check — and on the proxy validator, route it through a
    # different proxy entirely, so a broken credential would appear to work.
    owned = client is None
    http = client or httpx.Client(timeout=TIMEOUT, trust_env=False)
    try:
        return validators[kind](value, http)
    except httpx.HTTPError as exc:
        # Redacted: an HTTP client's error message quotes the request it was making, so a
        # credential can ride out in the *user-facing* detail — which lands in a response body,
        # where the logging filter never sees it.
        return ValidationResult(
            False, redact(f"could not reach the service to check this: {exc!s}")[:200])
    finally:
        if owned:
            http.close()


def _claude_result(response: httpx.Response) -> ValidationResult:
    """Shared reading of Anthropic's answer, so both auth styles fail identically."""
    if response.status_code == 200:
        return ValidationResult(True)
    if response.status_code in (401, 403):
        return ValidationResult(False, "Anthropic rejected this credential — check it was copied "
                                       "in full and has not been revoked")
    if response.status_code == 429:
        # Rate limited is not invalid. Treating it as invalid would tell a user with a perfectly
        # good key to go and replace it.
        return ValidationResult(True, "rate limited while checking; the credential itself is valid")
    if response.status_code == 402:
        return ValidationResult(False, "this account has no credit — top it up and try again")
    return ValidationResult(False, f"Anthropic answered {response.status_code} while checking")


def _validate_claude_api(value: str, http: httpx.Client) -> ValidationResult:
    """`GET /v1/models` — the cheapest authenticated call there is, and it costs no tokens."""
    return _claude_result(http.get(
        ANTHROPIC_URL,
        headers={"x-api-key": value, "anthropic-version": "2023-06-01"}))


def _validate_claude_oauth(value: str, http: httpx.Client) -> ValidationResult:
    """The same call on the other auth channel.

    OAuth tokens are short-lived, so this proves the current one is live and nothing more — the
    refresh material is stored alongside it and is what keeps it live.
    """
    return _claude_result(http.get(
        ANTHROPIC_URL,
        headers={"Authorization": f"Bearer {value}",
                 "anthropic-version": "2023-06-01",
                 "anthropic-beta": CLAUDE_OAUTH_BETA}))


def _validate_telegram(value: str, http: httpx.Client) -> ValidationResult:
    """`getMe`. Returns the bot's username so the form can show which bot was connected."""
    response = http.get(TELEGRAM_URL.format(token=value, method="getMe"))
    if response.status_code == 401:
        return ValidationResult(False, "Telegram rejected this token — check it was copied in full "
                                       "from BotFather")
    if response.status_code != 200:
        return ValidationResult(False, f"Telegram answered {response.status_code} while checking")

    payload = response.json()
    if not payload.get("ok"):
        return ValidationResult(
            False, redact(payload.get("description", "Telegram rejected this token")))
    username = (payload.get("result") or {}).get("username", "")
    return ValidationResult(True, extra={"bot_username": username} if username else None)


def _validate_proxy(value: str, http: httpx.Client) -> ValidationResult:
    """Prove the proxy both accepts us and forwards traffic.

    A credential that authenticates but reaches nothing is worse than one that fails outright: the
    LinkedIn loop would run, spend the user's own bandwidth allowance, and return nothing.
    """
    try:
        with httpx.Client(proxy=value, timeout=TIMEOUT, trust_env=False) as through:
            response = through.get("https://api.ipify.org", follow_redirects=False)
    except httpx.ProxyError as exc:
        return ValidationResult(False, redact(f"the proxy refused the connection: {exc!s}")[:200])
    except httpx.HTTPError as exc:
        return ValidationResult(
            False, redact(f"could not reach anything through the proxy: {exc!s}")[:200])

    if response.status_code == 407:
        return ValidationResult(False, "the proxy rejected these credentials")
    if response.status_code != 200:
        return ValidationResult(False, f"the proxy answered {response.status_code}")
    return ValidationResult(True, extra={"egress_ip": response.text.strip()[:64]})


def telegram_channel_check(token: str, channel_id: str, *,
                           client: httpx.Client | None = None) -> ValidationResult:
    """Prove the bot can post to this channel — by posting to it.

    A real `sendMessage`, not `getChat`: a bot must be an **administrator** of a channel to post,
    and the send is the only check that proves the one thing delivery needs. The message doubles as
    the user's confirmation on their own phone that the right channel is wired. Telegram's error
    strings for the failure modes ("chat not found", "not enough rights") are surfaced because each
    has a different one-minute fix.
    """
    owned = client is None
    http = client or httpx.Client(timeout=TIMEOUT, trust_env=False)
    try:
        response = http.post(
            TELEGRAM_URL.format(token=token, method="sendMessage"),
            json={"chat_id": channel_id,
                  "text": "Mahoraga connected — your jobs will stream here. 🛞"})
    except httpx.HTTPError as exc:
        return ValidationResult(False, redact(f"could not reach Telegram: {exc!s}")[:200])
    finally:
        if owned:
            http.close()

    payload = response.json() if response.headers.get("content-type", "").startswith(
        "application/json") else {}
    if response.status_code == 200 and payload.get("ok"):
        return ValidationResult(True)
    description = str(payload.get("description", f"Telegram answered {response.status_code}"))
    if "not found" in description.lower():
        return ValidationResult(False, "Telegram cannot find that channel — check the ID starts "
                                       "with -100, and that the bot has been added to the channel")
    if "rights" in description.lower() or "forbidden" in description.lower():
        return ValidationResult(False, "the bot is in the channel but cannot post — make it an "
                                       "administrator with permission to post messages")
    return ValidationResult(False, redact(description)[:200])


def telegram_chat_check(token: str, chat_id: str, *,
                        client: httpx.Client | None = None) -> ValidationResult:
    """Prove the bot can message this private chat — by messaging it.

    **The counterpart to `telegram_chat_id`, for when reading it fails.** Auto-capture depends on
    the update still being queued at Telegram and on nothing else having consumed it, neither of
    which the platform controls, and when it comes back empty the user is left pressing Start again
    with no way to proceed. A chat id they fetched themselves from @getidsbot always works.

    A send rather than `getChat`, for the same reason the channel check sends: a bot can look up a
    chat it is not allowed to write to, and writing is the only thing delivery needs. The message
    is also the user's confirmation on their own phone.
    """
    owned = client is None
    http = client or httpx.Client(timeout=TIMEOUT, trust_env=False)
    try:
        response = http.post(
            TELEGRAM_URL.format(token=token, method="sendMessage"),
            json={"chat_id": chat_id,
                  "text": "Mahoraga connected — commands and your finished CVs arrive here. 🛞"})
    except httpx.HTTPError as exc:
        return ValidationResult(False, redact(f"could not reach Telegram: {exc!s}")[:200])
    finally:
        if owned:
            http.close()

    payload = response.json() if response.headers.get("content-type", "").startswith(
        "application/json") else {}
    if response.status_code == 200 and payload.get("ok"):
        return ValidationResult(True)
    description = str(payload.get("description", f"Telegram answered {response.status_code}"))
    lowered = description.lower()
    if "not found" in lowered:
        return ValidationResult(False, "Telegram cannot find that chat — check the number, and "
                                       "that it is your own chat id rather than the channel's")
    if "blocked" in lowered or "forbidden" in lowered:
        return ValidationResult(False, "the bot cannot message you — open the bot in Telegram and "
                                       "press Start, then try again")
    return ValidationResult(False, redact(description)[:200])


def telegram_chat_id(token: str, *, client: httpx.Client | None = None) -> str | None:
    """The chat id of whoever last messaged this bot, or `None` if nobody has yet.

    **The user never has to find this themselves** (`TENANT_SAFETY.md` §2). They press Start in
    their bot and the platform reads it — the step that otherwise confuses people more than the rest
    of the setup put together.
    """
    owned = client is None
    http = client or httpx.Client(timeout=TIMEOUT, trust_env=False)
    try:
        response = http.get(TELEGRAM_URL.format(token=token, method="getUpdates"))
        if response.status_code != 200:
            logger.warning("Telegram answered %s to getUpdates; treating the chat id as not "
                           "yet available", response.status_code)
            return None
        for update in reversed((response.json() or {}).get("result") or []):
            chat = (update.get("message") or update.get("channel_post") or {}).get("chat") or {}
            if chat.get("id") is not None:
                return str(chat["id"])
        return None
    except httpx.HTTPError as exc:
        # Without this the user is told to press Start and try again, when in fact Telegram was
        # unreachable and pressing Start will not help.
        logger.warning("could not reach Telegram to read the chat id: %s", exc)
        return None
    finally:
        if owned:
            http.close()
