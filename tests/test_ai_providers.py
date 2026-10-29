"""Who pays for an AI request, and the Gemini and OpenAI adapters that make it on their key."""
from __future__ import annotations

import io
import json
import logging
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

httpx = pytest.importorskip("httpx")

from libs.llm import LlmError
from libs.llm.rest import GeminiClient, OpenAIClient

needs_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"),
                              reason="DATABASE_URL not set (no live Postgres)")

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
    """A provider added without a line here sends people's profiles somewhere they were not told."""
    from libs.legal import disclosure
    from libs.llm import PROVIDERS

    company = {"anthropic": "Anthropic", "gemini": "Google", "openai": "OpenAI"}
    processors = disclosure()["processors"]
    ai = [p for p in processors if any(company[name] in p["name"] for name in set(PROVIDERS.values()))]

    assert len(ai) == len(set(PROVIDERS.values()))
    # The platform's own key pays for some of these requests, so none may say it is always theirs.
    assert not any(p["on_your_credential"] for p in ai)


# ── who pays ──────────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def db(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from libs.db.models import User
    from libs.secrets import KeyRing, SecretStore, generate_key

    monkeypatch.delenv("PLATFORM_AI_PROVIDER", raising=False)
    monkeypatch.delenv("PLATFORM_AI_KEY", raising=False)
    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active")
    session.add(user)
    session.flush()
    try:
        yield session, user, SecretStore(session, KeyRing.from_env(
            {"SECRETS_KEK": f"k1:{generate_key()}"}))
    finally:
        session.close()
        trans.rollback()
        conn.close()


def _platform(monkeypatch):
    monkeypatch.setenv("PLATFORM_AI_PROVIDER", "gemini")
    monkeypatch.setenv("PLATFORM_AI_KEY", KEY)


@needs_db
def test_the_platform_pays_only_with_its_own_key_and_only_while_the_allowance_lasts(db,
                                                                                 monkeypatch):
    from libs.llm import PLATFORM, ai_requests_today, record_ai_request, resolve

    session, user, _store = db
    assert resolve(session, user) is None, "no platform key is configured, so nobody pays"

    _platform(monkeypatch)
    assert resolve(session, user).kind == PLATFORM
    for _ in range(19):
        record_ai_request(session, user.id)
    assert resolve(session, user).kind == PLATFORM, "twenty a day unless an operator says otherwise"

    record_ai_request(session, user.id)
    assert resolve(session, user) is None
    assert ai_requests_today(session, user.id) == 20


@needs_db
def test_their_own_key_comes_first_whatever_the_allowance_says(db, monkeypatch):
    from libs.limits import set_limit
    from libs.llm import resolve

    session, user, store = db
    _platform(monkeypatch)
    set_limit(session, user.id, "ai_platform_requests_per_day", 0)
    store.put(user.id, "openai_api", "sk-proj-" + "o" * 40)
    store.put(user.id, "gemini_api", "AIzaOwn-" + "g" * 30)
    session.flush()

    payer = resolve(session, user)

    # Claude first, then Gemini, then OpenAI: the order is fixed, not whichever was saved last.
    assert (payer.kind, payer.provider) == ("gemini_api", "gemini")


@needs_db
def test_yesterdays_requests_are_not_counted_today(db):
    from libs.db.models import AiRequest
    from libs.llm import ai_requests_today, record_ai_request

    session, user, _store = db
    record_ai_request(session, user.id)
    session.flush()
    session.query(AiRequest).filter(AiRequest.user_id == user.id).update(
        {"created_at": datetime.now(UTC) - timedelta(days=2)})

    assert ai_requests_today(session, user.id) == 0


@needs_db
def test_the_platform_allowance_can_be_switched_off_but_never_made_unlimited(db, monkeypatch):
    from libs.limits import set_limit
    from libs.llm import resolve

    session, user, _store = db
    _platform(monkeypatch)

    set_limit(session, user.id, "ai_platform_requests_per_day", 0)
    assert resolve(session, user) is None
    with pytest.raises(ValueError, match="cannot be unlimited"):
        set_limit(session, user.id, "ai_platform_requests_per_day", None)
    with pytest.raises(ValueError, match="at least 1"):
        set_limit(session, user.id, "cv_builds_per_day", 0)


@needs_db
def test_the_client_opened_is_the_payers_and_its_key_never_reaches_a_log(db, monkeypatch):
    from libs.llm import PLATFORM, paying_client
    from libs.secrets import install_on_handler

    session, user, store = db
    _platform(monkeypatch)
    own = "sk-proj-OwnKey-" + "o" * 30
    store.put(user.id, "openai_api", own)
    session.flush()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    install_on_handler(handler)
    logger = logging.getLogger("paying-client-test")
    logger.addHandler(handler)
    try:
        with paying_client(store, user.id, PLATFORM) as (client, provider):
            assert isinstance(client, GeminiClient) and provider == "gemini"
            logger.warning("calling with %s", KEY)
        with paying_client(store, user.id, "openai_api") as (client, provider):
            assert isinstance(client, OpenAIClient) and provider == "openai"
            logger.warning("calling with %s", own)
    finally:
        logger.removeHandler(handler)

    assert KEY not in stream.getvalue() and own not in stream.getvalue()
    store.delete(user.id, "openai_api")
    with paying_client(store, user.id, "openai_api") as paid:
        assert paid is None, "a key removed after the build was queued pays for nothing"
