"""One cheap call per credential kind, proving it works."""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from libs.secrets import redact

ANTHROPIC_URL = "https://api.anthropic.com/v1/models"
GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
OPENAI_MODELS_URL = "https://api.openai.com/v1/models"

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
        "gemini_api": _validate_gemini,
        "openai_api": _validate_openai,
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


def _validate_gemini(value: str, http: httpx.Client) -> ValidationResult:
    """Listing the models, which costs nothing. Google answers a wrong key with 400, not 401."""
    return _key_result(http.get(GEMINI_MODELS_URL, headers={"x-goog-api-key": value}),
                       "Google", rejected=(400, 401, 403))


def _validate_openai(value: str, http: httpx.Client) -> ValidationResult:
    """Listing the models, which costs nothing."""
    return _key_result(http.get(OPENAI_MODELS_URL, headers={"Authorization": f"Bearer {value}"}),
                       "OpenAI")
