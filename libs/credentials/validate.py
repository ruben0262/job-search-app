"""One cheap call per credential kind, proving it works."""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from libs.llm.client import CLAUDE_OAUTH_BETA
from libs.secrets import redact

ANTHROPIC_URL = "https://api.anthropic.com/v1/models"
TELEGRAM_URL = "https://api.telegram.org/bot{token}/{method}"

TIMEOUT = 15.0


@dataclass(frozen=True)
class ValidationResult:
    """Whether the credential works, and what to tell the user if it does not."""

    ok: bool
    detail: str = ""
    # Anything else the check learned — the bot's name, the address a proxy sends from.
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


def _claude_result(response: httpx.Response) -> ValidationResult:
    """Shared reading of Anthropic's answer, so both auth styles fail identically."""
    if response.status_code == 200:
        return ValidationResult(True)
    if response.status_code in (401, 403):
        return ValidationResult(False, "Anthropic rejected this credential — check it was copied "
                                       "in full and has not been revoked")
    if response.status_code == 429:
        # Rate limited is not invalid.
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
    """The same call on the other auth channel."""
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
