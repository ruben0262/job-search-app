"""Checking an API key with its provider at the moment it is linked (`libs.credentials`). The routes that
link one are `tests/test_ai_accounts.py`."""
from __future__ import annotations

import io
import logging

import pytest

pytest.importorskip("httpx")
pytest.importorskip("cryptography")

import httpx

from libs.credentials import validate_credential
from libs.secrets import install_on_handler

API_KEY = "sk-ant-api03-" + "Zx9" * 20


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)


# ── how each provider's answers are read ──────────────────────────────────────────────────────

def test_a_valid_api_key_is_accepted_and_sent_on_the_right_header():
    """An API key goes on `x-api-key`."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json={"data": []})

    assert validate_credential("claude_api", API_KEY, client=_client(handler)).ok
    assert seen["x-api-key"] == API_KEY
    assert "authorization" not in seen


def test_being_rate_limited_does_not_mean_the_credential_is_bad():
    """Treating 429 as invalid would tell a user with a perfectly good key to go and replace it."""
    result = validate_credential(
        "claude_api", API_KEY,
        client=_client(lambda r: httpx.Response(429, json={"error": {}})))
    assert result.ok is True


@pytest.mark.parametrize("status,phrase", [(401, "rejected"), (403, "rejected"), (402, "credit")])
def test_a_rejected_key_says_something_the_user_can_act_on(status, phrase):
    """"402" is not actionable; "this account has no credit" is."""
    result = validate_credential(
        "claude_api", API_KEY, client=_client(lambda r: httpx.Response(status, json={})))
    assert result.ok is False
    assert phrase in result.detail


GEMINI_KEY = "AIzaSy" + "Gm7" * 11
OPENAI_KEY = "sk-proj-" + "Op4" * 15


def test_a_gemini_key_is_checked_on_its_own_header_and_a_wrong_one_is_refused():
    """Google answers a wrong key with 400, not 401 — read as "rejected", not as an outage."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers, url=str(request.url))
        return httpx.Response(200, json={"models": []})

    assert validate_credential("gemini_api", GEMINI_KEY, client=_client(handler)).ok
    assert seen["x-goog-api-key"] == GEMINI_KEY
    assert GEMINI_KEY not in seen["url"], "a key in the URL ends up in every error message"

    wrong = validate_credential("gemini_api", GEMINI_KEY, client=_client(
        lambda r: httpx.Response(400, json={"error": {"message": "API key not valid."}})))
    assert wrong.ok is False and "rejected" in wrong.detail


def test_an_openai_key_is_checked_with_bearer_and_a_wrong_one_is_refused():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json={"data": []})

    assert validate_credential("openai_api", OPENAI_KEY, client=_client(handler)).ok
    assert seen["authorization"] == f"Bearer {OPENAI_KEY}"

    wrong = validate_credential("openai_api", OPENAI_KEY,
                                client=_client(lambda r: httpx.Response(401, json={})))
    assert wrong.ok is False and "rejected" in wrong.detail


def test_an_unreachable_provider_is_not_a_bad_credential():
    """A user with a working key must not be told to replace it because Anthropic was down."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    result = validate_credential("claude_api", API_KEY, client=_client(handler))
    assert result.ok is False
    assert "could not reach" in result.detail


def test_an_unknown_credential_kind_is_refused():
    assert validate_credential("aws_secret", "whatever-value").ok is False


# ── the credential never escapes ──────────────────────────────────────────────────────────────

def test_a_failed_validation_does_not_put_the_credential_in_the_log():
    """The traceback an HTTP client raises on a failed call is the single place a credential most often."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    install_on_handler(handler)
    logger = logging.getLogger("credential-test")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)

    from libs.secrets import redacting

    def failing(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"failed connecting while sending {API_KEY}")

    try:
        with redacting(API_KEY):
            result = validate_credential("claude_api", API_KEY, client=_client(failing))
            logger.warning("validation failed: %s", result.detail)
    finally:
        logger.removeHandler(handler)

    assert API_KEY not in stream.getvalue()
    assert API_KEY not in result.detail, "the message shown to the user must not echo the key"
