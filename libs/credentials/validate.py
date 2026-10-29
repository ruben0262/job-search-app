"""One cheap call per credential kind, proving it works."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from libs.llm.client import CLAUDE_OAUTH_BETA
from libs.secrets import redact

ANTHROPIC_URL = "https://api.anthropic.com/v1/models"
GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
OPENAI_MODELS_URL = "https://api.openai.com/v1/models"
TELEGRAM_URL = "https://api.telegram.org/bot{token}/{method}"

TIMEOUT = 15.0

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ValidationResult:
    """Whether the credential works, and what to tell the user if it does not."""

    ok: bool
    detail: str = ""
    # Anything the check learned that the platform needs — the Telegram chat id, for instance.
    extra: dict[str, str] | None = None


def validate_credential(kind: str, value: str, *,
                        client: httpx.Client | None = None) -> ValidationResult:
    """Dispatch to the right validator. `client` is injectable so tests need no network."""
    validators = {
        "claude_api": _validate_claude_api,
        "claude_oauth": _validate_claude_oauth,
        "gemini_api": _validate_gemini,
        "openai_api": _validate_openai,
        "telegram_bot": _validate_telegram,
        "proxy": _validate_proxy,
    }
    if kind not in validators:
        return ValidationResult(False, f"{kind!r} is not a credential this platform stores")

    # `trust_env=False`: an ambient HTTP_PROXY would silently route the check somewhere else.
    owned = client is None
    http = client or httpx.Client(timeout=TIMEOUT, trust_env=False)
    try:
        return validators[kind](value, http)
    except httpx.HTTPError as exc:
        # Redacted: an HTTP client's error quotes the request, so a credential can ride out in the detail.
        return ValidationResult(
            False, redact(f"could not reach the service to check this: {exc!s}")[:200])
    finally:
        if owned:
            http.close()


def _key_result(response: httpx.Response, provider: str, *,
                rejected: tuple[int, ...] = (401, 403)) -> ValidationResult:
    """One reading of a provider's answer to "list your models", so every key fails alike."""
    if response.status_code == 200:
        return ValidationResult(True)
    if response.status_code in rejected:
        return ValidationResult(False, f"{provider} rejected this credential — check it was copied "
                                       "in full and has not been revoked")
    if response.status_code == 429:
        # Rate limited is not invalid.
        return ValidationResult(True, "rate limited while checking; the credential itself is valid")
    if response.status_code == 402:
        return ValidationResult(False, "this account has no credit — top it up and try again")
    return ValidationResult(False, f"{provider} answered {response.status_code} while checking")


def _validate_claude_api(value: str, http: httpx.Client) -> ValidationResult:
    """`GET /v1/models` — the cheapest authenticated call there is, and it costs no tokens."""
    return _key_result(http.get(
        ANTHROPIC_URL,
        headers={"x-api-key": value, "anthropic-version": "2023-06-01"}), "Anthropic")


def _validate_claude_oauth(value: str, http: httpx.Client) -> ValidationResult:
    """The same call on the other auth channel."""
    return _key_result(http.get(
        ANTHROPIC_URL,
        headers={"Authorization": f"Bearer {value}",
                 "anthropic-version": "2023-06-01",
                 "anthropic-beta": CLAUDE_OAUTH_BETA}), "Anthropic")


def _validate_gemini(value: str, http: httpx.Client) -> ValidationResult:
    """Listing the models, which costs nothing. Google answers a wrong key with 400, not 401."""
    return _key_result(http.get(GEMINI_MODELS_URL, headers={"x-goog-api-key": value}),
                       "Google", rejected=(400, 401, 403))


def _validate_openai(value: str, http: httpx.Client) -> ValidationResult:
    """Listing the models, which costs nothing."""
    return _key_result(http.get(OPENAI_MODELS_URL, headers={"Authorization": f"Bearer {value}"}),
                       "OpenAI")


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
    """Prove the proxy both accepts us and forwards traffic."""
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
    """Prove the bot can post to this channel — by posting to it."""
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
    """Prove the bot can message this private chat — by messaging it."""
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
    """The chat id of whoever last messaged this bot, or `None` if nobody has yet."""
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
        # Otherwise the user is told to press Start when in fact Telegram was unreachable.
        logger.warning("could not reach Telegram to read the chat id: %s", exc)
        return None
    finally:
        if owned:
            http.close()
