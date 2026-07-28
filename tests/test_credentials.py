"""Credential validation and the routes that store it.

The validators are driven against a stub transport, not the network: what is worth testing is how
each provider's answers are *read* — rate-limited is not invalid, no-credit is not a bad key — and
whether a credential can escape into a response or a log.
"""
from __future__ import annotations

import io
import logging
import os
import uuid

import pytest

pytest.importorskip("httpx")
pytest.importorskip("cryptography")

import httpx

from libs.credentials import CLAUDE_OAUTH_BETA, telegram_chat_id, validate_credential
from libs.secrets import install_on_handler

API_KEY = "sk-ant-api03-" + "Zx9" * 20
OAUTH_TOKEN = "sk-ant-oat01-" + "Qq4" * 20
BOT_TOKEN = "7123456789:AAF" + "q7Kd2mNp" * 4


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)


# ── how each provider's answers are read ──────────────────────────────────────────────────────

def test_a_valid_api_key_is_accepted_and_sent_on_the_right_header():
    """An API key goes on `x-api-key`. Sending it as a Bearer token is the most common way a
    working credential looks broken."""
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


def test_an_unreachable_provider_is_not_a_bad_credential():
    """A user with a working key must not be told to replace it because Anthropic was down."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    result = validate_credential("claude_api", API_KEY, client=_client(handler))
    assert result.ok is False
    assert "could not reach" in result.detail


def test_a_telegram_token_returns_the_bot_it_belongs_to():
    """Shown back on the form, so a user who has two bots can see which one they just connected."""
    result = validate_credential(
        "telegram_bot", BOT_TOKEN,
        client=_client(lambda r: httpx.Response(
            200, json={"ok": True, "result": {"username": "my_jobs_bot"}})))
    assert result.ok
    assert result.extra["bot_username"] == "my_jobs_bot"


def test_an_unknown_credential_kind_is_refused():
    assert validate_credential("aws_secret", "whatever-value").ok is False


# ── the chat id the user never has to find ────────────────────────────────────────────────────

def test_the_chat_id_is_read_from_the_most_recent_message():
    """`TENANT_SAFETY.md` §2 — this is the step that confuses people more than the rest of the setup
    put together, so the platform reads it rather than asking."""
    updates = {"ok": True, "result": [
        {"message": {"chat": {"id": 111}}},
        {"message": {"chat": {"id": 222}}},
    ]}
    assert telegram_chat_id(
        BOT_TOKEN, client=_client(lambda r: httpx.Response(200, json=updates))) == "222"


def test_no_messages_yet_is_not_an_error():
    """They saved a valid token and have not pressed Start. Normal, and the flow says so."""
    assert telegram_chat_id(
        BOT_TOKEN,
        client=_client(lambda r: httpx.Response(200, json={"ok": True, "result": []}))) is None


# ── the credential never escapes ──────────────────────────────────────────────────────────────

def test_a_failed_validation_does_not_put_the_credential_in_the_log():
    """The traceback an HTTP client raises on a failed call is the single place a credential most
    often escapes — it quotes the request it was making."""
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

pytestmark_db = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (no live Postgres)")


@pytest.fixture()
def api():
    """The real app with an authenticated caller, a rolled-back session, and no network."""
    pytest.importorskip("fastapi")
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    os.environ["DISABLE_RATE_LIMIT"] = "1"

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from libs.auth.access import issue_access_token
    from libs.db.models import User
    from libs.secrets import KeyRing, SecretStore, generate_key
    from services.api import deps
    from services.api.app import app

    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")

    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active")
    session.add(user)
    session.flush()

    store = SecretStore(session, KeyRing.from_env({"SECRETS_KEK": f"k1:{generate_key()}"}))
    app.dependency_overrides[deps.get_db] = lambda: session
    app.dependency_overrides[deps.get_store] = lambda: store
    try:
        with TestClient(app, base_url="https://testserver") as http:
            http.cookies.set("access_token",
                             issue_access_token(user.id, uuid.uuid4()), path="/")
            yield http, session, user, store
    finally:
        app.dependency_overrides.clear()
        session.close()
        trans.rollback()
        conn.close()
        os.environ.pop("DISABLE_RATE_LIMIT", None)


@pytestmark_db
def test_a_bad_credential_is_refused_at_the_form(api, monkeypatch):
    """The whole point of validating at save: it fails at a form field rather than at 3 a.m."""
    from libs.credentials import ValidationResult

    http, _session, _, _ = api
    monkeypatch.setattr("services.api.credential_routes.validate_credential",
                        lambda kind, value, **kw: ValidationResult(False, "Anthropic rejected this"))

    response = http.put("/credentials/claude_api", json={"value": API_KEY})
    assert response.status_code == 422
    assert "rejected" in response.json()["detail"]


@pytestmark_db
def test_an_unreachable_provider_can_be_overridden(api, monkeypatch):
    """A provider outage must not stop a user finishing onboarding. Stored unvalidated, and the
    account page says so."""
    from libs.credentials import ValidationResult

    http, session, _, _ = api
    monkeypatch.setattr("services.api.credential_routes.validate_credential",
                        lambda kind, value, **kw: ValidationResult(False, "could not reach"))

    response = http.put("/credentials/claude_api",
                        json={"value": API_KEY, "allow_unvalidated": True})
    session.flush()
    assert response.status_code == 200
    assert response.json()["validated"] is False

    listed = http.get("/credentials").json()
    assert listed["credentials"][0]["validated"] is False


@pytestmark_db
def test_the_list_never_returns_a_credential_value(api, monkeypatch):
    """Write-only through the API. The one property this endpoint exists to preserve."""
    from libs.credentials import ValidationResult

    http, session, _, _ = api
    monkeypatch.setattr("services.api.credential_routes.validate_credential",
                        lambda kind, value, **kw: ValidationResult(True))

    http.put("/credentials/claude_api", json={"value": API_KEY})
    session.flush()

    body = http.get("/credentials").text
    assert API_KEY not in body
    assert API_KEY[-4:] in body, "last_4 is shown so a user can recognise which key they saved"


@pytestmark_db
def test_either_claude_credential_satisfies_the_requirement(api, monkeypatch):
    """`claude_api` and `claude_oauth` are two adapters behind one requirement (D2). The front end
    must not have to re-derive that rule and get it subtly wrong."""
    from libs.credentials import ValidationResult

    http, session, _, _ = api
    monkeypatch.setattr("services.api.credential_routes.validate_credential",
                        lambda kind, value, **kw: ValidationResult(True))

    assert http.get("/credentials").json()["requirements"]["claude"] is False
    http.put("/credentials/claude_oauth", json={"value": OAUTH_TOKEN})
    session.flush()
    assert http.get("/credentials").json()["requirements"]["claude"] is True


@pytestmark_db
def test_removing_the_telegram_token_disconnects_delivery(api, monkeypatch):
    """Leaving the account `connected` with no token would queue notify jobs that can only fail, and
    each would look like a delivery problem rather than a missing credential."""
    from libs.credentials import ValidationResult

    http, session, user, _ = api
    monkeypatch.setattr("services.api.credential_routes.validate_credential",
                        lambda kind, value, **kw: ValidationResult(True))
    monkeypatch.setattr("services.api.credential_routes.telegram_chat_id",
                        lambda token, **kw: "12345")

    http.put("/credentials/telegram_bot", json={"value": BOT_TOKEN})
    session.flush()
    assert user.telegram_status == "connected"
    assert user.telegram_chat_id == "12345"

    http.delete("/credentials/telegram_bot")
    session.flush()
    assert user.telegram_status == "unconfigured"
    assert user.telegram_chat_id is None


@pytestmark_db
def test_signing_in_is_required(api):
    """Without it, anyone could read which credentials an account has — or replace them."""
    http, _, _, _ = api
    http.cookies.clear()
    assert http.get("/credentials").status_code == 401
