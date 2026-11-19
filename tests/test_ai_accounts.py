"""The AI accounts a person links in Settings (owner, 2026-11-09): sign-ins to their own Claude, ChatGPT or
Google account, and API keys. Several, one in use, never shown back; without one in use, no document.
The routes are the real app on the test database; the providers' checks and the sign-in broker are
stand-ins (`tests/test_credentials.py` and `tests/test_signin.py` are theirs)."""
from __future__ import annotations

import base64
import json
import os
import uuid
from datetime import UTC, datetime

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
    ".claude.json": json.dumps({"oauthAccount": {"emailAddress": "sam@example.com"}})})


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
    user = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x",
                           subscription_status="active", status="active",
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
    assert set(account) == {"id", "kind", "label", "in_use", "linked_at", "model", "plan_limits"}
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
    stranger = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x",
                               subscription_status="active", status="active"))
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
        return {"status": "done", "secret": CLAUDE_LOGIN, "label": "Claude Max · sam@example.com"}

    monkeypatch.setattr("services.api.ai_account_routes._broker", broker)
    started = http.post("/ai-accounts/signin/claude_login").json()
    assert started["url"].startswith("https://claude.ai/") and asked[0][1]["kind"] == "claude_login"

    done = http.post("/ai-accounts/signin/claude_login/finish",
                     json={"session_id": started["session_id"], "code": "abcd1234#efgh"})

    (account,) = done.json()["accounts"]["accounts"]
    assert (account["label"], account["in_use"]) == ("Claude Max · sam@example.com", True)
    assert "sk-ant-oat01" not in done.text and "refreshToken" not in http.get("/ai-accounts").text
    assert store.reveal_account(user.id, ObjectId(account["id"])).reveal() == CLAUDE_LOGIN


@needs_db
def test_a_chatgpt_sign_in_waits_until_openais_page_has_the_code(api, monkeypatch):
    http, _, _, _ = api
    answers = iter([{"status": "waiting"}, {"status": "done", "secret": json.dumps({"auth.json": "{}"}),
                                           "label": "ChatGPT Plus · sam@example.com"}])
    monkeypatch.setattr("services.api.ai_account_routes._broker",
                        lambda path, payload: {"session_id": "s" * 16, "url": "https://auth.openai.com/codex/device",
                                               "code": "8CXI-BU5CD"} if path == "/start" else next(answers))

    started = http.post("/ai-accounts/signin/codex_login").json()
    assert started["code"] == "8CXI-BU5CD", "shown here, to be entered on OpenAI's page"
    first = http.post("/ai-accounts/signin/codex_login/finish", json={"session_id": started["session_id"]})
    second = http.post("/ai-accounts/signin/codex_login/finish", json={"session_id": started["session_id"]})

    assert first.json() == {"status": "waiting", "accounts": None}
    assert second.json()["accounts"]["accounts"][0]["label"] == "ChatGPT Plus · sam@example.com"


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

    alice = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x",
                            subscription_status="active"))
    bob = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x",
                          subscription_status="active"))
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


# ── a plan's limits ───────────────────────────────────────────────────────────────────────────

def _signed_in(expires_ms: float | None = None) -> str:
    login = {"accessToken": "sk-ant-oat01-" + "a" * 40, "refreshToken": "sk-ant-ort01-" + "r" * 40}
    if expires_ms is not None:
        login["expiresAt"] = expires_ms
    return json.dumps({".credentials.json": json.dumps({"claudeAiOauth": login})})


def _anthropic(answer):
    import httpx

    seen: list = []

    def handler(request):
        seen.append(request)
        return answer(request)

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


USAGE = {"five_hour": {"utilization": 37.0, "resets_at": "2026-11-15T03:00:00Z"},
         "seven_day": {"utilization": 12.5, "resets_at": "2026-11-19T09:00:00+00:00"},
         "seven_day_opus": None, "extra_usage": {"is_enabled": False}}


def test_a_claude_sign_in_shows_its_five_hour_and_weekly_limits():
    """Owner, 2026-11-14: the plan's limits in Settings, the 5-hour one and the weekly one, as Claude Code's
    own /usage shows them. Asked with the token the sign-in holds, which never leaves in a log line."""
    import httpx

    from libs.llm import limits

    http, seen = _anthropic(lambda request: httpx.Response(200, json=USAGE))

    windows = limits.read("claude_login", _signed_in(), http=http)

    assert [(each.id, each.label, each.percent) for each in windows] == [
        ("five_hour", "5-hour limit", 37.0), ("seven_day", "Weekly limit", 12.5)]
    assert [each.resets_at.isoformat() for each in windows] == ["2026-11-15T03:00:00+00:00",
                                                                "2026-11-19T09:00:00+00:00"]
    assert str(seen[0].url) == limits.USAGE_URL
    assert seen[0].headers["authorization"] == "Bearer sk-ant-oat01-" + "a" * 40


def test_limits_that_cannot_be_read_are_none_and_never_cost_the_sign_in(caplog):
    """No tool is run for them, so nothing can renew a sign-in and leave the renewal unsaved, which retires
    it: a probe did that on 2026-11-14. A token that ran out is not even asked with; a refusal, a key, and
    an answer in another shape show nothing, and say so in the log without the token."""
    import httpx

    from libs.llm import limits

    refused, asked = _anthropic(lambda request: httpx.Response(401, json={"error": "expired"}))
    odd, _ = _anthropic(lambda request: httpx.Response(200, json={"limits": []}))
    past = 1_000.0  # an expiry long gone, in milliseconds

    with caplog.at_level("WARNING", logger="libs.llm.limits"):
        assert limits.read("claude_login", _signed_in(expires_ms=past), http=refused) == []
        assert asked == [], "a token that ran out is left alone"
        assert limits.read("claude_login", _signed_in(), http=refused) == []
        assert limits.read("claude_login", _signed_in(), http=odd) == []
        assert limits.read("claude_api", "sk-ant-api03-key", http=refused) == []
        assert limits.read("codex_login", json.dumps({"auth.json": "{}"}), http=refused) == []

    assert len(asked) == 1 and "answered 401" in caplog.text and "no window we know" in caplog.text
    assert "sk-ant-oat01" not in caplog.text


def _chatgpt(expires: float) -> str:
    """A ChatGPT sign-in as Codex leaves it: its access token a JWT, which carries its own expiry."""
    claims = base64.urlsafe_b64encode(json.dumps({"exp": expires}).encode()).decode().rstrip("=")
    return json.dumps({"auth.json": json.dumps({"auth_mode": "chatgpt", "tokens": {
        "access_token": f"eyJhbGciOiJSUzI1NiJ9.{claims}.c2lnbmF0dXJl", "account_id": "acct-7",
        "refresh_token": "rt_" + "r" * 40}})})


# What ChatGPT answered a Plus plan on 2026-11-19, but for who it is.
CHATGPT_USAGE = {"plan_type": "plus", "rate_limit": {
    "allowed": True, "limit_reached": False,
    "primary_window": {"used_percent": 99, "limit_window_seconds": 18000, "reset_after_seconds": 7391,
                       "reset_at": 1795105188},
    "secondary_window": {"used_percent": 17, "limit_window_seconds": 604800, "reset_after_seconds": 274646,
                         "reset_at": 1795372443}}, "credits": {"has_credits": False}}


def test_a_chatgpt_sign_in_shows_its_five_hour_and_weekly_limits_and_a_spent_token_is_left_alone():
    """Owner, 2026-11-19: GPT's usage shown as Claude's is. Read as Codex's own /status reads it, with the
    token the sign-in holds and the account it names, and called by the same names; never asked with a token
    whose time is up, which only the next document renews."""
    import httpx

    from libs.llm import limits

    http, seen = _anthropic(lambda request: httpx.Response(200, json=CHATGPT_USAGE))

    windows = limits.read("codex_login", _chatgpt(expires=4_000_000_000), http=http)

    assert [(each.id, each.label, each.percent) for each in windows] == [
        ("five_hour", "5-hour limit", 99.0), ("seven_day", "Weekly limit", 17.0)]
    assert windows[0].resets_at == datetime.fromtimestamp(1795105188, UTC)
    assert str(seen[0].url) == limits.CODEX_USAGE_URL
    assert seen[0].headers["authorization"].startswith("Bearer eyJ")
    assert seen[0].headers["chatgpt-account-id"] == "acct-7"

    assert limits.read("codex_login", _chatgpt(expires=1_000), http=http) == []
    assert len(seen) == 1, "a token that ran out is left alone"


@needs_db
def test_settings_asks_for_an_accounts_limits_and_only_its_owner_may(api, monkeypatch):
    http, _, user, store = api
    from libs.llm import limits

    monkeypatch.setattr(limits, "read", lambda kind, blob, **kw: [
        limits.Window("five_hour", "5-hour limit", 37.0, None)] if kind == "claude_login" else [])
    account = store.add(user.id, "claude_login", CLAUDE_LOGIN, label="Claude Max · sam@example.com")
    key = _key(http).json()["accounts"][0]["id"]

    assert http.get(f"/ai-accounts/{account}/limits").json() == {"windows": [
        {"id": "five_hour", "label": "5-hour limit", "percent": 37.0, "resets_at": None}]}
    assert http.get(f"/ai-accounts/{key}/limits").json() == {"windows": []}, "a key has no plan"
    # Said with each account, so Settings shows a plan's limits where there are some and nowhere else.
    assert {each["id"]: each["plan_limits"] for each in http.get("/ai-accounts").json()["accounts"]} == {
        str(account): True, key: False}
    assert http.get("/ai-accounts/not-an-id/limits").status_code == 404
    assert http.get(f"/ai-accounts/{'0' * 24}/limits").status_code == 404
