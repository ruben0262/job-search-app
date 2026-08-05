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


@pytestmark_db
def test_the_channel_is_proven_by_posting_not_trusted(api, monkeypatch):
    """The one check that matches what delivery needs: a bot must be an admin to post to a
    channel, so the test send is the proof — and its failure strings are the user's fix."""
    from libs.credentials import ValidationResult

    http, session, user, _ = api
    monkeypatch.setattr("services.api.credential_routes.validate_credential",
                        lambda kind, value, **kw: ValidationResult(True))
    monkeypatch.setattr("services.api.credential_routes.telegram_chat_id",
                        lambda token, **kw: "12345")
    http.put("/credentials/telegram_bot", json={"value": BOT_TOKEN})

    monkeypatch.setattr("services.api.credential_routes.telegram_channel_check",
                        lambda token, channel, **kw: ValidationResult(True))
    response = http.put("/credentials/telegram/channel", json={"channel_id": "-1001234567890"})
    assert response.status_code == 200
    session.flush()
    session.refresh(user)
    assert user.telegram_routes == {"hu": "-1001234567890", "linkedin": "-1001234567890",
                                    "other": "-1001234567890"}

    # A failed post surfaces Telegram's reason, and stores nothing.
    monkeypatch.setattr(
        "services.api.credential_routes.telegram_channel_check",
        lambda token, channel, **kw: ValidationResult(False, "the bot is in the channel but "
                                                            "cannot post"))
    refused = http.put("/credentials/telegram/channel", json={"channel_id": "-1009999999999"})
    assert refused.status_code == 400
    session.refresh(user)
    assert user.telegram_routes["other"] == "-1001234567890"

    # And removing it falls delivery back to the private chat rather than dropping anything.
    assert http.delete("/credentials/telegram/channel").status_code == 200
    session.flush()
    session.refresh(user)
    assert user.telegram_routes is None


@pytestmark_db
def test_a_channel_id_that_is_not_one_is_refused_by_shape(api):
    http, _, _, _ = api
    assert http.put("/credentials/telegram/channel",
                    json={"channel_id": "1234567890"}).status_code == 422


@pytestmark_db
def test_reveal_needs_the_password_and_returns_the_value_once(api, monkeypatch):
    """The one exception to write-only, gated the way deletion is: a stolen session cookie is not
    enough, the attempt is audited either way, and nothing else ever returns a value."""
    from libs.auth.passwords import hash_password
    from libs.credentials import ValidationResult

    http, session, user, _ = api
    user.password_hash = hash_password("correct horse battery staple")
    monkeypatch.setattr("services.api.credential_routes.validate_credential",
                        lambda kind, value, **kw: ValidationResult(True))
    http.put("/credentials/proxy", json={"value": "http://user:pass@proxy.example:8080"})
    session.flush()

    wrong = http.post("/credentials/proxy/reveal", json={"password": "not the password"})
    assert wrong.status_code == 401
    assert "value" not in wrong.json()

    right = http.post("/credentials/proxy/reveal", json={"password": "correct horse battery staple"})
    assert right.status_code == 200
    assert right.json()["value"] == "http://user:pass@proxy.example:8080"

    nothing = http.post("/credentials/telegram_bot/reveal", json={"password": "correct horse battery staple"})
    assert nothing.status_code == 404


@pytestmark_db
def test_an_oauth_token_that_does_not_work_is_never_stored(api, monkeypatch):
    """**Unlike a pasted credential, this one is refused rather than kept unvalidated.**

    A rendering fault once dropped a character from the minted token, and because the route stored
    it anyway the account ended up holding a credential that could not make a single request while
    the page said it was connected. There is no typo to preserve here: the platform produced this
    value itself, so a token that does not authenticate is a bug on our side, not a user's guess.
    """
    from libs.credentials import ValidationResult
    from libs.tenant.repository import TenantRepository

    http, session, user, _ = api
    monkeypatch.setattr("services.api.credential_routes._broker",
                        lambda path, payload: {"token": "sk-ant-at01-" + "x" * 40})
    monkeypatch.setattr("services.api.credential_routes.validate_credential",
                        lambda kind, value, **kw: ValidationResult(False, "Anthropic rejected it."))

    refused = http.post("/credentials/claude/oauth/finish",
                        json={"session_id": "s" * 12, "code": "abcd1234"})
    assert refused.status_code == 502
    assert "Nothing was saved" in refused.json()["detail"]
    session.flush()
    assert not [row for row in TenantRepository(session).secret_metadata(user.id)
                if row.kind == "claude_oauth"]


@pytestmark_db
def test_a_failed_reconnect_leaves_the_working_token_alone(api, monkeypatch):
    """Re-connecting with a lapsed subscription must not cost somebody the credential they had."""
    from libs.credentials import ValidationResult
    from libs.tenant.repository import TenantRepository

    http, session, user, _ = api
    monkeypatch.setattr("services.api.credential_routes.validate_credential",
                        lambda kind, value, **kw: ValidationResult(True))
    http.put("/credentials/claude_oauth", json={"value": "sk-ant-oat01-" + "w" * 40})
    session.flush()

    monkeypatch.setattr("services.api.credential_routes._broker",
                        lambda path, payload: {"token": "sk-ant-oat01-" + "n" * 40})
    monkeypatch.setattr("services.api.credential_routes.validate_credential",
                        lambda kind, value, **kw: ValidationResult(False, "no active subscription."))
    assert http.post("/credentials/claude/oauth/finish",
                     json={"session_id": "s" * 12, "code": "abcd1234"}).status_code == 502
    session.flush()

    stored = [row for row in TenantRepository(session).secret_metadata(user.id)
              if row.kind == "claude_oauth"]
    assert stored and stored[0].validated_at is not None, "the working credential must survive"


@pytestmark_db
def test_the_chat_id_can_be_set_by_hand_when_reading_it_fails(api, monkeypatch):
    """**The way through when auto-capture cannot work.**

    `telegram/connect` reads whoever last messaged the bot, which needs the update to still be
    queued at Telegram and nothing else to have consumed it. When it comes back empty there was
    previously nothing else to try: the page could not complete, and onboarding stopped at a step
    whose only instruction was to press Start again -- which had already been done.
    """
    from libs.credentials import ValidationResult

    http, session, user, _ = api
    monkeypatch.setattr("services.api.credential_routes.validate_credential",
                        lambda kind, value, **kw: ValidationResult(True))
    monkeypatch.setattr("services.api.credential_routes.telegram_chat_id", lambda token, **kw: None)
    http.put("/credentials/telegram_bot", json={"value": "123456:AAturtles"})
    session.flush()
    assert user.telegram_chat_id is None, "auto-capture found nothing, which is the premise"

    monkeypatch.setattr("services.api.credential_routes.telegram_chat_check",
                        lambda token, chat_id, **kw: ValidationResult(True))
    saved = http.put("/credentials/telegram/chat", json={"chat_id": "123456789"})
    assert saved.status_code == 200
    session.flush()
    assert user.telegram_chat_id == "123456789"
    assert user.telegram_status == "connected"


@pytestmark_db
def test_a_chat_id_the_bot_cannot_reach_is_refused(api, monkeypatch):
    """Proven by sending, like the channel. A bot can look up a chat it may not write to, and
    writing is the only thing delivery needs."""
    from libs.credentials import ValidationResult

    http, session, user, _ = api
    monkeypatch.setattr("services.api.credential_routes.validate_credential",
                        lambda kind, value, **kw: ValidationResult(True))
    monkeypatch.setattr("services.api.credential_routes.telegram_chat_id", lambda token, **kw: None)
    http.put("/credentials/telegram_bot", json={"value": "123456:AAturtles"})
    monkeypatch.setattr("services.api.credential_routes.telegram_chat_check",
                        lambda token, chat_id, **kw: ValidationResult(False, "bot was blocked"))

    refused = http.put("/credentials/telegram/chat", json={"chat_id": "123456789"})
    assert refused.status_code == 400
    session.flush()
    assert user.telegram_chat_id is None
    assert user.telegram_status != "connected"


@pytestmark_db
def test_a_channel_id_pasted_into_the_chat_field_is_caught_by_shape(api):
    """The two numbers sit on the same page and the mistake is easy. A channel id is negative and
    starts -100; a personal chat id never is."""
    http, _session, _user, _ = api
    assert http.put("/credentials/telegram/chat",
                    json={"chat_id": "-1004454061394"}).status_code == 422


# ── the webhook that nothing registered (ROADMAP 6af) ─────────────────────────────────────────


@pytestmark_db
def test_connecting_a_bot_registers_its_webhook_and_stores_the_secret(api, monkeypatch):
    """**The button that did nothing, for every account but one.**

    `telegram_webhook_secret` was declared on the model, created by a migration and read by the
    webhook route — and written by nothing. Taps arrived, matched no user, 404'd in silence, and
    "Build my CV" looked broken because it was. This is the producer that was missing.
    """
    import httpx as _httpx

    from services.api import credential_routes

    http, session, user, store = api
    monkeypatch.setenv("PUBLIC_URL", "https://app.example.com")
    store.put(user.id, "telegram_bot", "123:ABC")
    session.flush()

    called = {}

    def post(url, **kwargs):
        called["url"] = url
        called["json"] = kwargs.get("json")
        return _httpx.Response(200, json={"ok": True, "result": True})

    monkeypatch.setattr(credential_routes.httpx, "post", post)
    monkeypatch.setattr(credential_routes, "telegram_chat_id", lambda _token: "4242")

    body = http.post("/credentials/telegram/connect").json()

    assert body["status"] == "connected"
    assert body["webhook"] == "registered"
    session.refresh(user)
    secret = user.telegram_webhook_secret
    assert secret, "the row must carry the secret, or no tap can be matched to this account"
    # Telegram is told the same secret twice: once as the path it posts to, once as the header it
    # sends. The route checks both.
    assert called["json"]["url"].endswith(f"/telegram/webhook/{secret}")
    assert called["json"]["secret_token"] == secret
    assert "callback_query" in called["json"]["allowed_updates"]


@pytestmark_db
def test_reconnecting_repairs_an_account_that_has_no_secret_yet(api, monkeypatch):
    """Every account created before this existed has a live bot and a null secret. Pressing Connect
    again has to be the repair, or each one needs a hand-written database row."""
    import httpx as _httpx

    from services.api import credential_routes

    http, session, user, store = api
    monkeypatch.setenv("PUBLIC_URL", "https://app.example.com")
    store.put(user.id, "telegram_bot", "123:ABC")
    user.telegram_status = "connected"
    user.telegram_chat_id = "4242"
    session.flush()
    assert user.telegram_webhook_secret is None

    monkeypatch.setattr(credential_routes.httpx, "post",
                        lambda url, **kw: _httpx.Response(200, json={"ok": True}))
    monkeypatch.setattr(credential_routes, "telegram_chat_id", lambda _token: "4242")

    http.post("/credentials/telegram/connect")

    session.refresh(user)
    assert user.telegram_webhook_secret


@pytestmark_db
def test_a_telegram_outage_does_not_block_connecting(api, monkeypatch):
    """Registering is best-effort: the secret is stored either way and pressing Connect again
    retries. A bad minute at Telegram must not strand somebody mid-onboarding."""
    import httpx as _httpx

    from services.api import credential_routes

    http, session, user, store = api
    monkeypatch.setenv("PUBLIC_URL", "https://app.example.com")
    store.put(user.id, "telegram_bot", "123:ABC")
    session.flush()

    def boom(url, **kwargs):
        raise _httpx.ConnectError("no route to host")

    monkeypatch.setattr(credential_routes.httpx, "post", boom)
    monkeypatch.setattr(credential_routes, "telegram_chat_id", lambda _token: "4242")

    body = http.post("/credentials/telegram/connect").json()

    assert body["status"] == "connected"
    assert body["webhook"] == "unreachable"
    session.refresh(user)
    assert user.telegram_webhook_secret


@pytestmark_db
def test_a_tap_that_matches_no_account_is_logged_rather_than_dropped(api, caplog):
    """The 404 was silent, which is how a dead feature stayed dead. A tap arriving for a webhook
    with no owner is the one symptom the platform gets, so it has to be written down."""
    import logging as _logging

    http, _session, _user, _store = api

    with caplog.at_level(_logging.WARNING):
        response = http.post("/telegram/webhook/nosuchsecretatall",
                             json={"callback_query": {}},
                             headers={"X-Telegram-Bot-Api-Secret-Token": "nosuchsecretatall"})

    assert response.status_code == 404
    assert any("matched no account" in record.message for record in caplog.records)
