"""Credential validation and the routes that store it."""
from __future__ import annotations

import io
import logging
import os
import uuid

import pytest

pytest.importorskip("httpx")
pytest.importorskip("cryptography")

import httpx

from libs.credentials import CLAUDE_OAUTH_BETA, validate_credential
from libs.secrets import install_on_handler
from tests.conftest import needs_db

API_KEY = "sk-ant-api03-" + "Zx9" * 20
OAUTH_TOKEN = "sk-ant-oat01-" + "Qq4" * 20
BOT_TOKEN = "7123456789:AAF" + "q7Kd2mNp" * 4


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


def test_an_oauth_token_uses_bearer_and_the_beta_header():
    """The other channel, and it needs the beta header — without it the same token is rejected."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json={"data": []})

    assert validate_credential("claude_oauth", OAUTH_TOKEN, client=_client(handler)).ok
    assert seen["authorization"] == f"Bearer {OAUTH_TOKEN}"
    assert seen["anthropic-beta"] == CLAUDE_OAUTH_BETA
    assert "x-api-key" not in seen


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


# ── the routes ────────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def api(db, monkeypatch):
    """The real app on the test database, with an authenticated caller and no network."""
    pytest.importorskip("fastapi")
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")

    from fastapi.testclient import TestClient

    from libs.auth.access import issue_access_token
    from libs.db import insert
    from libs.db.models import User
    from libs.secrets import KeyRing, SecretStore, generate_key
    from services.api import deps
    from services.api.app import app

    user = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active"))

    store = SecretStore(db, KeyRing.from_env({"SECRETS_KEK": f"k1:{generate_key()}"}))
    app.dependency_overrides[deps.get_store] = lambda: store
    try:
        with TestClient(app, base_url="https://testserver") as http:
            http.cookies.set("access_token",
                             issue_access_token(user.id, uuid.uuid4()), path="/")
            yield http, db, user, store
    finally:
        app.dependency_overrides.clear()


@needs_db
def test_a_bad_credential_is_refused_at_the_form(api, monkeypatch):
    """The whole point of validating at save: it fails at a form field rather than at 3 a.m."""
    from libs.credentials import ValidationResult

    http, _db, _, _ = api
    monkeypatch.setattr("services.api.credential_routes.validate_credential",
                        lambda kind, value, **kw: ValidationResult(False, "Anthropic rejected this"))

    response = http.put("/credentials/claude_api", json={"value": API_KEY})
    assert response.status_code == 422
    assert "rejected" in response.json()["detail"]


@needs_db
def test_an_unreachable_provider_can_be_overridden(api, monkeypatch):
    """A provider outage must not stop a user finishing onboarding."""
    from libs.credentials import ValidationResult

    http, _db, _, _ = api
    monkeypatch.setattr("services.api.credential_routes.validate_credential",
                        lambda kind, value, **kw: ValidationResult(False, "could not reach"))

    response = http.put("/credentials/claude_api",
                        json={"value": API_KEY, "allow_unvalidated": True})
    assert response.status_code == 200
    assert response.json()["validated"] is False

    listed = http.get("/credentials").json()
    assert listed["credentials"][0]["validated"] is False


@needs_db
def test_a_gemini_or_an_openai_key_can_be_saved(api, monkeypatch):
    """The two kinds AI documents run on, through the same form as the Claude ones."""
    from libs.credentials import ValidationResult

    http, _db, _, _ = api
    monkeypatch.setattr("services.api.credential_routes.validate_credential",
                        lambda kind, value, **kw: ValidationResult(True))

    for kind, value in (("gemini_api", GEMINI_KEY), ("openai_api", OPENAI_KEY)):
        assert http.put(f"/credentials/{kind}", json={"value": value}).status_code == 200

    kinds = {row["kind"] for row in http.get("/credentials").json()["credentials"]}
    assert {"gemini_api", "openai_api"} <= kinds


@needs_db
def test_the_list_never_returns_a_credential_value(api, monkeypatch):
    """Write-only through the API. The one property this endpoint exists to preserve."""
    from libs.credentials import ValidationResult

    http, _db, _, _ = api
    monkeypatch.setattr("services.api.credential_routes.validate_credential",
                        lambda kind, value, **kw: ValidationResult(True))

    http.put("/credentials/claude_api", json={"value": API_KEY})

    body = http.get("/credentials").text
    assert API_KEY not in body
    assert API_KEY[-4:] in body, "last_4 is shown so a user can recognise which key they saved"


@needs_db
def test_a_bot_token_is_no_longer_a_credential_and_cannot_touch_the_link(api, monkeypatch):
    """Telegram is linked to the platform's bot now, so a per-user token is not stored at all — and
    asking to store or remove one neither connects nor disconnects anybody."""
    from libs.credentials import ValidationResult
    from libs.db import get
    from libs.db.models import User, UserSecret

    http, db, user, _ = api
    monkeypatch.setattr("services.api.credential_routes.validate_credential",
                        lambda kind, value, **kw: ValidationResult(True))
    db[User.COLLECTION].update_one({"_id": user.id}, {"$set": {"telegram_status": "connected",
                                                                "telegram_chat_id": "4242"}})

    assert http.put("/credentials/telegram_bot", json={"value": BOT_TOKEN}).status_code == 400
    assert http.delete("/credentials/telegram_bot").status_code == 400
    assert db[UserSecret.COLLECTION].count_documents({"user_id": user.id}) == 0
    stored = get(db, User, user.id)
    assert (stored.telegram_status, stored.telegram_chat_id) == ("connected", "4242")


@needs_db
def test_signing_in_is_required(api):
    """Without it, anyone could read which credentials an account has — or replace them."""
    http, _, _, _ = api
    http.cookies.clear()
    assert http.get("/credentials").status_code == 401


@needs_db
def test_an_oauth_token_that_does_not_work_is_never_stored(api, monkeypatch):
    """Unlike a pasted credential, this one is refused rather than kept unvalidated."""
    from libs.credentials import ValidationResult
    from libs.tenant.repository import TenantRepository

    http, db, user, _ = api
    monkeypatch.setattr("services.api.credential_routes._broker",
                        lambda path, payload: {"token": "sk-ant-at01-" + "x" * 40})
    monkeypatch.setattr("services.api.credential_routes.validate_credential",
                        lambda kind, value, **kw: ValidationResult(False, "Anthropic rejected it."))

    refused = http.post("/credentials/claude/oauth/finish",
                        json={"session_id": "s" * 12, "code": "abcd1234"})
    assert refused.status_code == 502
    assert "Nothing was saved" in refused.json()["detail"]
    assert not [row for row in TenantRepository(db).secret_metadata(user.id)
                if row.kind == "claude_oauth"]


@needs_db
def test_a_failed_reconnect_leaves_the_working_token_alone(api, monkeypatch):
    """Re-connecting with a lapsed subscription must not cost somebody the credential they had."""
    from libs.credentials import ValidationResult
    from libs.tenant.repository import TenantRepository

    http, db, user, _ = api
    monkeypatch.setattr("services.api.credential_routes.validate_credential",
                        lambda kind, value, **kw: ValidationResult(True))
    http.put("/credentials/claude_oauth", json={"value": "sk-ant-oat01-" + "w" * 40})

    monkeypatch.setattr("services.api.credential_routes._broker",
                        lambda path, payload: {"token": "sk-ant-oat01-" + "n" * 40})
    monkeypatch.setattr("services.api.credential_routes.validate_credential",
                        lambda kind, value, **kw: ValidationResult(False, "no active subscription."))
    assert http.post("/credentials/claude/oauth/finish",
                     json={"session_id": "s" * 12, "code": "abcd1234"}).status_code == 502

    stored = [row for row in TenantRepository(db).secret_metadata(user.id)
              if row.kind == "claude_oauth"]
    assert stored and stored[0].validated_at is not None, "the working credential must survive"
