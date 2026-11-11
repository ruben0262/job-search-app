"""Who pays for an AI request, and the Gemini and OpenAI adapters that make it on their key."""
from __future__ import annotations

import io
import json
import logging
import uuid
from datetime import timedelta

import pytest

httpx = pytest.importorskip("httpx")

from libs.llm import LlmError
from libs.llm.rest import GeminiClient, OpenAIClient
from tests.conftest import needs_db

KEY = "AIzaSyTestKey-" + "k" * 26
MESSAGES = [{"role": "user", "content": "Write the CV."}]


def _http(answer):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answer(request)

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def _ask(adapter, http, **kwargs):
    return adapter(KEY, http=http).complete_text(
        model=kwargs.pop("model", "the-model"), system="Facts.", messages=MESSAGES,
        max_tokens=500, **kwargs)


# ── the adapters ──────────────────────────────────────────────────────────────────────────────

def test_gemini_is_asked_on_its_own_header_and_only_the_answer_comes_back():
    http, seen = _http(lambda r: httpx.Response(200, json={"candidates": [{"content": {"parts": [
        {"text": "Let me think.", "thought": True}, {"text": "<html>CV</html>"}]},
        "finishReason": "STOP"}]}))

    assert _ask(GeminiClient, http, model="gemini-x") == "<html>CV</html>"

    body = json.loads(seen[0].content)
    assert str(seen[0].url).endswith("/models/gemini-x:generateContent")
    assert seen[0].headers["x-goog-api-key"] == KEY and KEY not in str(seen[0].url)
    assert body["systemInstruction"] == {"parts": [{"text": "Facts."}]}
    assert body["contents"] == [{"role": "user", "parts": [{"text": "Write the CV."}]}]
    assert body["generationConfig"]["maxOutputTokens"] == 500


def test_openai_is_asked_with_the_instructions_first_and_the_answer_comes_back():
    http, seen = _http(lambda r: httpx.Response(200, json={"choices": [{
        "message": {"content": "<html>CV</html>"}, "finish_reason": "stop"}]}))

    assert _ask(OpenAIClient, http, model="gpt-x") == "<html>CV</html>"

    body = json.loads(seen[0].content)
    assert seen[0].headers["authorization"] == f"Bearer {KEY}"
    assert body["model"] == "gpt-x" and body["max_completion_tokens"] == 500
    assert body["messages"] == [{"role": "developer", "content": "Facts."},
                                {"role": "user", "content": "Write the CV."}]


@pytest.mark.parametrize("adapter", [GeminiClient, OpenAIClient])
@pytest.mark.parametrize(("status", "final"),
                         [(400, True), (401, True), (403, True), (404, True),
                          (429, False), (500, False), (503, False)])
def test_a_refusal_is_final_and_a_busy_provider_is_tried_again(adapter, status, final):
    """`LlmError` ends the build with the provider's own reason; anything else is retried by the queue."""
    http, _ = _http(lambda r: httpx.Response(status, json={"error": {"message": "their reason"}}))

    with pytest.raises(Exception) as raised:
        _ask(adapter, http)

    assert isinstance(raised.value, LlmError) is final
    assert "their reason" in str(raised.value)


def test_an_answer_with_no_text_is_a_refusal_rather_than_an_empty_document():
    http, _ = _http(lambda r: httpx.Response(200, json={
        "candidates": [], "promptFeedback": {"blockReason": "SAFETY"}}))

    with pytest.raises(LlmError, match="SAFETY"):
        _ask(GeminiClient, http)


def test_a_reply_cut_off_at_the_ceiling_is_said_out_loud(caplog):
    http, _ = _http(lambda r: httpx.Response(200, json={"choices": [{
        "message": {"content": "<html>half a CV"}, "finish_reason": "length"}]}))

    with caplog.at_level(logging.ERROR, logger="libs.llm.client"):
        _ask(OpenAIClient, http)

    assert "truncated" in caplog.text


def test_every_provider_a_profile_can_be_sent_to_is_named_in_the_privacy_disclosure():
    """A provider added without a line here sends people's profiles somewhere they were not told. A sign-in
    and an API key of one company's send to that company alike: ChatGPT's sign-in (Codex) is OpenAI's."""
    from libs.legal import disclosure
    from libs.llm.accounts import PROVIDER

    company = {"anthropic": "Anthropic", "gemini": "Google", "openai": "OpenAI", "codex": "OpenAI"}
    names = {company[provider] for provider in PROVIDER.values()}
    ai = [p for p in disclosure()["processors"] if any(name in p["name"] for name in names)]

    assert len(ai) == len(names) == 3
    # The platform's own key pays for some of these requests, so none may say it is always theirs.
    assert not any(p["on_your_credential"] for p in ai)


# ── who pays ──────────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def ctx(db, monkeypatch):
    from libs.db import insert
    from libs.db.models import User
    from libs.secrets import KeyRing, SecretStore, generate_key

    monkeypatch.delenv("PLATFORM_AI_PROVIDER", raising=False)
    monkeypatch.delenv("PLATFORM_AI_KEY", raising=False)
    user = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active"))
    return db, user, SecretStore(db, KeyRing.from_env({"SECRETS_KEK": f"k1:{generate_key()}"}))


def _fresh(db, user):
    """The account as stored now: `resolve` reads the limits off the document it is handed."""
    from libs.db import get
    from libs.db.models import User

    return get(db, User, user.id)


def _platform(monkeypatch):
    monkeypatch.setenv("PLATFORM_AI_PROVIDER", "gemini")
    monkeypatch.setenv("PLATFORM_AI_KEY", KEY)


@needs_db
def test_the_platform_pays_only_with_its_own_key_and_only_while_the_allowance_lasts(ctx,
                                                                                 monkeypatch):
    from libs.llm import PLATFORM, ai_requests_today, record_ai_request, resolve

    db, user, _store = ctx
    assert resolve(db, user) is None, "no platform key is configured, so nobody pays"

    _platform(monkeypatch)
    assert resolve(db, user).kind == PLATFORM
    for _ in range(19):
        record_ai_request(db, user.id)
    assert resolve(db, user).kind == PLATFORM, "twenty a day unless an operator says otherwise"

    record_ai_request(db, user.id)
    assert resolve(db, user) is None
    assert ai_requests_today(db, user.id) == 20


def _link(db, store, user, kind, value):
    from libs.llm import accounts

    account = store.add(user.id, kind, value, label=accounts.KIND_NAMES[kind], validated=True)
    accounts.use(db, user.id, str(account))
    return str(account)


@needs_db
def test_the_chat_runs_on_their_api_key_in_use_whatever_the_allowance_says(ctx, monkeypatch):
    """Not on a signed-in account: the chat calls tools, which a sign-in's tool does not offer."""
    from libs.limits import set_limit
    from libs.llm import PLATFORM, resolve

    db, user, store = ctx
    _platform(monkeypatch)
    set_limit(db, user.id, "ai_platform_requests_per_day", 0)
    gemini = _link(db, store, user, "gemini_api", "AIzaOwn-" + "g" * 30)

    payer = resolve(db, _fresh(db, user))
    assert (payer.kind, payer.provider) == (gemini, "gemini")

    _link(db, store, user, "claude_login", json.dumps({".credentials.json": "{}"}))
    set_limit(db, user.id, "ai_platform_requests_per_day", 5)
    assert resolve(db, _fresh(db, user)).kind == PLATFORM, "a sign-in in use: the chat on the allowance"


@needs_db
def test_a_document_is_written_only_with_their_own_account_in_use(ctx, monkeypatch):
    """However much of the platform's allowance is left (owner, 2026-11-09)."""
    from libs.llm import accounts, own

    db, user, store = ctx
    _platform(monkeypatch)
    assert own(db, user) is None

    claude = _link(db, store, user, "claude_login", json.dumps({".credentials.json": "{}"}))
    assert (own(db, user).kind, own(db, user).provider) == (claude, "anthropic")
    accounts.stop(db, user.id, claude)
    assert own(db, user) is None, "switched off: none"


@needs_db
def test_yesterdays_requests_are_not_counted_today(ctx):
    from libs.db import now
    from libs.db.models import AiRequest
    from libs.llm import ai_requests_today, record_ai_request

    db, user, _store = ctx
    record_ai_request(db, user.id)
    assert ai_requests_today(db, user.id) == 1, "the premise"
    db[AiRequest.COLLECTION].update_many({"user_id": user.id},
                                         {"$set": {"created_at": now() - timedelta(days=2)}})

    assert ai_requests_today(db, user.id) == 0


@needs_db
def test_the_platform_allowance_can_be_switched_off_but_never_made_unlimited(ctx, monkeypatch):
    from libs.limits import set_limit
    from libs.llm import resolve

    db, user, _store = ctx
    _platform(monkeypatch)

    assert resolve(db, user) is not None, "the premise: the platform would pay"
    set_limit(db, user.id, "ai_platform_requests_per_day", 0)
    assert resolve(db, _fresh(db, user)) is None
    with pytest.raises(ValueError, match="cannot be unlimited"):
        set_limit(db, user.id, "ai_platform_requests_per_day", None)
    with pytest.raises(ValueError, match="at least 1"):
        set_limit(db, user.id, "cv_builds_per_day", 0)


@needs_db
def test_the_client_opened_is_the_payers_and_its_key_never_reaches_a_log(ctx, monkeypatch):
    from libs.llm import PLATFORM, paying_client
    from libs.secrets import install_on_handler

    db, user, store = ctx
    _platform(monkeypatch)
    own = "sk-proj-OwnKey-" + "o" * 30
    openai = _link(db, store, user, "openai_api", own)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    install_on_handler(handler)
    logger = logging.getLogger("paying-client-test")
    logger.addHandler(handler)
    try:
        with paying_client(store, user.id, PLATFORM) as (client, provider):
            assert isinstance(client, GeminiClient) and provider == "gemini"
            logger.warning("calling with %s", KEY)
        with paying_client(store, user.id, openai) as (client, provider):
            assert isinstance(client, OpenAIClient) and provider == "openai"
            logger.warning("calling with %s", own)
    finally:
        logger.removeHandler(handler)

    assert KEY not in stream.getvalue() and own not in stream.getvalue()
    from libs.llm import accounts

    accounts.unlink(db, user.id, openai)
    with paying_client(store, user.id, openai) as paid:
        assert paid is None, "an account removed after the build was queued pays for nothing"
    with paying_client(store, user.id, "not-an-account") as paid:
        assert paid is None


@needs_db
def test_a_signed_in_account_opens_its_own_tool_and_a_renewed_login_is_sealed_again(ctx, monkeypatch):
    from bson import ObjectId

    from libs.llm import paying_client
    from libs.llm.cli import CliClient

    db, user, store = ctx
    first = json.dumps({".credentials.json": json.dumps({"token": "first"})})
    claude = _link(db, store, user, "claude_login", first)
    renewed = json.dumps({".credentials.json": json.dumps({"token": "renewed"})})

    with paying_client(store, user.id, claude) as (client, provider):
        assert isinstance(client, CliClient) and provider == "anthropic"
        client._on_refresh(renewed)  # what a call does when the tool renewed the login

    assert store.reveal_account(user.id, ObjectId(claude)).reveal() == renewed


@pytest.mark.parametrize(("model", "thinking", "effort"), [
    ("claude-haiku-5-5", {"type": "adaptive"}, {"effort": "medium"}),
    ("claude-sonnet-4-6", {"type": "adaptive"}, {"effort": "medium"}),
    ("claude-haiku-4-5", {"type": "enabled", "budget_tokens": 4096}, None),
    ("claude-haiku-4-5-20251001", {"type": "enabled", "budget_tokens": 4096}, None),
    ("claude-sonnet-4-20250514", {"type": "enabled", "budget_tokens": 4096}, None)])
def test_each_claude_model_is_asked_to_think_the_way_it_can(model, thinking, effort):
    """Haiku 5.5, the CV model since 2026-11-10, answers a token budget with a 400: from 4.6 on, Claude
    thinks adaptively, as hard as the effort asks, and 4.5 and before take no effort level. The request as
    the SDK is handed it; the SDK itself is the docgen image's."""
    from types import SimpleNamespace

    from libs.llm.client import AnthropicClient

    sent: list[dict] = []

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def get_final_message(self):
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="<html></html>")],
                                   stop_reason="end_turn")

    client = AnthropicClient.__new__(AnthropicClient)
    client._client = SimpleNamespace(
        messages=SimpleNamespace(stream=lambda **asked: sent.append(asked) or Stream()))

    client.complete_text(model=model, system="s", messages=[{"role": "user", "content": "p"}],
                         max_tokens=32_000, effort="medium")

    assert sent[0]["thinking"] == thinking
    assert sent[0].get("output_config") == effort
