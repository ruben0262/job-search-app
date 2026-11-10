"""The AI accounts a person links in Settings (owner, 2026-11-09): sign-ins to their own Claude, ChatGPT or
Google account, and API keys. Several, one in use, never shown back; without one in use, no document.
The routes are the real app on the test database; the providers' checks and the sign-in broker are
stand-ins (`tests/test_credentials.py` and `tests/test_signin.py` are theirs)."""
from __future__ import annotations

import json
import os
import uuid

import pytest

pytest.importorskip("fastapi")

from tests.conftest import needs_db

API_KEY = "sk-ant-api03-" + "Zx9" * 20
GEMINI_KEY = "AIzaSy" + "Gm7" * 11
# What a Claude sign-in leaves: Claude Code's login files, as `libs.llm.cli` keeps them.
CLAUDE_LOGIN = json.dumps({
    ".credentials.json": json.dumps({"claudeAiOauth": {
        "accessToken": "sk-ant-oat01-" + "a" * 40, "refreshToken": "sk-ant-ort01-" + "r" * 40,
        "subscriptionType": "max"}}),
    ".claude.json": json.dumps({"oauthAccount": {"emailAddress": "ruben@example.com"}})})


@pytest.fixture()
def api(db, monkeypatch):
    """The real app, an authenticated caller, keys that every provider accepts, and no network."""
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")
    from fastapi.testclient import TestClient

    from libs.auth.access import issue_access_token
    from libs.credentials import ValidationResult
    from libs.db import insert, now
    from libs.db.models import User
    from libs.secrets import KeyRing, SecretStore, generate_key
    from services.api import deps
    from services.api.app import app

    monkeypatch.setattr("services.api.ai_account_routes.validate_credential",
                        lambda kind, value, **kw: ValidationResult(True))
    user = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active",
                           onboarding_completed_at=now()))
    store = SecretStore(db, KeyRing.from_env({"SECRETS_KEK": f"k1:{generate_key()}"}))
    app.dependency_overrides[deps.get_store] = lambda: store
    try:
        with TestClient(app, base_url="https://testserver") as http:
            http.cookies.set("access_token", issue_access_token(user.id, uuid.uuid4()), path="/")
            yield http, db, user, store
    finally:
        app.dependency_overrides.clear()


def _key(http, kind="claude_api", value=API_KEY, **extra):
    return http.post("/ai-accounts/keys", json={"kind": kind, "value": value, **extra})


@needs_db
def test_a_key_is_checked_linked_put_in_use_and_never_shown_back(api):
    """Nothing of a key comes back, not even its last four characters (owner: never displayed)."""
    http, _, _, _ = api

    linked = _key(http)

    assert linked.status_code == 200
    (account,) = linked.json()["accounts"]
    assert set(account) == {"id", "kind", "label", "in_use", "linked_at", "model"}
    assert (account["kind"], account["label"], account["in_use"]) == ("claude_api", "Claude API key", True)
    shown = http.get("/ai-accounts").text + linked.text
    assert API_KEY not in shown and API_KEY[-4:] not in shown


@needs_db
def test_a_bad_key_is_refused_at_the_form_and_an_unreachable_provider_can_be_overridden(api, monkeypatch):
    """Checked when it is linked, so it fails at a form field rather than at a CV; but an outage at the
    provider must not stop someone linking the key they know is right."""
    from libs.credentials import ValidationResult

    http, _, _, _ = api
    monkeypatch.setattr("services.api.ai_account_routes.validate_credential",
                        lambda kind, value, **kw: ValidationResult(False, "Anthropic rejected this"))

    refused = _key(http)
    assert refused.status_code == 422 and "rejected" in refused.json()["detail"]
    assert http.get("/ai-accounts").json()["accounts"] == []
    assert _key(http, allow_unvalidated=True).status_code == 200
    assert len(http.get("/ai-accounts").json()["accounts"]) == 1


@needs_db
def test_several_can_be_linked_and_switching_one_on_switches_the_others_off(api):
    """The one in use writes every document; the database itself refuses a second one in use."""
    from pymongo.errors import DuplicateKeyError

    from libs.db.models import UserSecret

    http, db, user, _ = api
    _key(http)
    accounts = _key(http, "gemini_api", GEMINI_KEY).json()["accounts"]
    assert [(each["kind"], each["in_use"]) for each in accounts] == [
        ("gemini_api", True), ("claude_api", False)], "the newest is in use, and listed first"

    claude = accounts[1]["id"]
    switched = http.put(f"/ai-accounts/{claude}", json={"in_use": True}).json()["accounts"]
    assert [(each["kind"], each["in_use"]) for each in switched] == [
        ("gemini_api", False), ("claude_api", True)], "the switch moves, not the row"

    stopped = http.put(f"/ai-accounts/{claude}", json={"in_use": False}).json()["accounts"]
    assert not any(each["in_use"] for each in stopped), "switched off, none is in use"

    with pytest.raises(DuplicateKeyError):
        db[UserSecret.COLLECTION].update_many({"user_id": user.id}, {"$set": {"active": True}})


@needs_db
def test_each_account_says_the_model_it_writes_with_the_one_admin_picked(api):
    """One model a provider, chosen by the platform (owner, 2026-11-10), and shown where it is used: the
    name the docgen worker will ask for. A ChatGPT sign-in's is its plan's, which Codex picks."""
    from libs.settings import DEFAULT_DOCUMENT_MODELS, put

    http, db, _, _ = api
    (claude,) = _key(http).json()["accounts"]
    assert claude["model"] == DEFAULT_DOCUMENT_MODELS["anthropic"]

    put(db, "document_models", {**DEFAULT_DOCUMENT_MODELS, "anthropic": "claude-sonnet-5-5"})

    assert http.get("/ai-accounts").json()["accounts"][0]["model"] == "claude-sonnet-5-5"


@needs_db
def test_removing_one_forgets_it_and_its_secret(api):
    from libs.db.models import UserSecret

    http, db, user, _ = api
    (account,) = _key(http).json()["accounts"]

    assert http.delete(f"/ai-accounts/{account['id']}").json()["accounts"] == []
    assert db[UserSecret.COLLECTION].count_documents({"user_id": user.id}) == 0


@needs_db
def test_another_persons_account_cannot_be_switched_or_removed(api):
    from libs.auth.access import issue_access_token
    from libs.db import insert
    from libs.db.models import User

    http, db, _, _ = api
    (account,) = _key(http).json()["accounts"]
    stranger = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active"))
    http.cookies.set("access_token", issue_access_token(stranger.id, uuid.uuid4()), path="/")

    assert http.get("/ai-accounts").json()["accounts"] == []
    assert http.put(f"/ai-accounts/{account['id']}", json={"in_use": False}).status_code == 404
    assert http.delete(f"/ai-accounts/{account['id']}").status_code == 404
    assert http.put("/ai-accounts/not-an-id", json={"in_use": True}).status_code == 404


@needs_db
def test_a_sign_in_is_sealed_named_by_its_account_and_put_in_use_and_its_login_never_shown(api, monkeypatch):
    from bson import ObjectId

    http, _, user, store = api
    asked: list[tuple[str, dict]] = []

    def broker(path, payload):
        asked.append((path, payload))
        if path == "/start":
            return {"session_id": "s" * 16, "url": "https://claude.ai/oauth/authorize?x=1", "code": None}
        return {"status": "done", "secret": CLAUDE_LOGIN, "label": "Claude Max · ruben@example.com"}

    monkeypatch.setattr("services.api.ai_account_routes._broker", broker)
    started = http.post("/ai-accounts/signin/claude_login").json()
    assert started["url"].startswith("https://claude.ai/") and asked[0][1]["kind"] == "claude_login"

    done = http.post("/ai-accounts/signin/claude_login/finish",
                     json={"session_id": started["session_id"], "code": "abcd1234#efgh"})

    (account,) = done.json()["accounts"]["accounts"]
    assert (account["label"], account["in_use"]) == ("Claude Max · ruben@example.com", True)
    assert "sk-ant-oat01" not in done.text and "refreshToken" not in http.get("/ai-accounts").text
    assert store.reveal_account(user.id, ObjectId(account["id"])).reveal() == CLAUDE_LOGIN


@needs_db
def test_a_chatgpt_sign_in_waits_until_openais_page_has_the_code(api, monkeypatch):
    http, _, _, _ = api
    answers = iter([{"status": "waiting"}, {"status": "done", "secret": json.dumps({"auth.json": "{}"}),
                                           "label": "ChatGPT Plus · ruben@example.com"}])
    monkeypatch.setattr("services.api.ai_account_routes._broker",
                        lambda path, payload: {"session_id": "s" * 16, "url": "https://auth.openai.com/codex/device",
                                               "code": "8CXI-BU5CD"} if path == "/start" else next(answers))

    started = http.post("/ai-accounts/signin/codex_login").json()
    assert started["code"] == "8CXI-BU5CD", "shown here, to be entered on OpenAI's page"
    first = http.post("/ai-accounts/signin/codex_login/finish", json={"session_id": started["session_id"]})
    second = http.post("/ai-accounts/signin/codex_login/finish", json={"session_id": started["session_id"]})

    assert first.json() == {"status": "waiting", "accounts": None}
    assert second.json()["accounts"]["accounts"][0]["label"] == "ChatGPT Plus · ruben@example.com"


@needs_db
def test_a_sign_in_the_broker_cannot_start_says_so_and_cancel_is_not_taken_for_a_kind(api, monkeypatch):
    import httpx

    http, _, _, _ = api

    def down(*_args, **_kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr("services.api.ai_account_routes.httpx.post", down)
    refused = http.post("/ai-accounts/signin/gemini_login")
    assert refused.status_code == 503 and "unavailable" in refused.json()["detail"]
    assert http.post("/ai-accounts/signin/telegram").status_code == 422
    monkeypatch.setattr("services.api.ai_account_routes._broker", lambda path, payload: {})
    cancelled = http.post("/ai-accounts/signin/cancel", json={"session_id": "s" * 16})
    assert cancelled.json() == {"status": "cancelled"}


@needs_db
def test_signing_in_is_required(api):
    http, _, _, _ = api
    http.cookies.clear()
    assert http.get("/ai-accounts").status_code == 401
    assert _key(http).status_code == 401


@needs_db
def test_accounts_linked_before_the_change_are_settled_once(db):
    """What the deploy runs: keys get a label and one goes in use, a sign-in first; the old Claude tokens
    from `claude setup-token` are deleted; a Telegram bot token is left alone; running it again does
    nothing."""
    from libs.db import insert, now
    from libs.db.models import User, UserSecret
    from libs.llm.accounts import settle

    alice = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x"))
    bob = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x"))
    rows = [{"user_id": alice.id, "kind": "gemini_api"}, {"user_id": alice.id, "kind": "claude_api"},
            {"user_id": bob.id, "kind": "claude_oauth"}, {"user_id": bob.id, "kind": "telegram_bot"}]
    # As the old rules wrote them: no label, no switch, and a kind the new rules no longer allow.
    db.command("collMod", UserSecret.COLLECTION, validationLevel="off")
    db[UserSecret.COLLECTION].insert_many([
        {**row, "ciphertext": b"x", "nonce": b"n", "updated_at": now()} for row in rows])

    assert settle(db) == {"labelled": 2, "switched": 1, "removed": 1}
    left = {row["kind"]: row for row in db[UserSecret.COLLECTION].find(
        {"user_id": {"$in": [alice.id, bob.id]}})}
    assert set(left) == {"gemini_api", "claude_api", "telegram_bot"}
    assert left["claude_api"]["active"] and not left["gemini_api"].get("active")
    assert (left["claude_api"]["label"], left["gemini_api"]["label"]) == ("Claude API key", "Gemini API key")
    assert "label" not in left["telegram_bot"]
    assert settle(db) == {"labelled": 0, "switched": 0, "removed": 0}
