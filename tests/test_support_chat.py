"""The help chat: asked on the site, answered by a scripted model through the real loop, streamed
back as Server-Sent Events, and able to change an account only through a button the person taps.

The model is the one stand-in. The guard, the allowance, the consent gate, the stream and the
restart are the code that runs in production, against a real MongoDB.
"""
from __future__ import annotations

import copy
import json
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

PLATFORM_KEY = "AIzaPlatformKey-" + "p" * 24
CONFIG = {"sections": [{"platform": "all", "keywords": ["data engineer"],
                        "exclude_keywords": ["senior"]}]}


class Scripted:
    """The runner the tests use: `complete_tools` as every provider has it, free, instant, offline.
    Each turn is `(text, [(tool, input), ...])`; its text streams a word at a time, `delay` apart."""

    def __init__(self, *turns, delay: float = 0.0):
        self.turns, self.delay, self.seen = list(turns), delay, []

    def complete_tools(self, *, model, system, messages, tools, max_tokens, on_text=None):
        self.seen.append({"model": model, "system": system, "messages": copy.deepcopy(messages),
                          "tools": [tool["name"] for tool in tools]})
        said, calls = self.turns.pop(0)
        for word in re.findall(r"\S+\s*", said):
            time.sleep(self.delay)
            on_text(word)
        uses = [{"type": "tool_use", "id": f"call-{len(self.seen)}-{n}", "name": name, "input": args}
                for n, (name, args) in enumerate(calls)]
        return {"text": said, "tool_uses": [{key: use[key] for key in ("id", "name", "input")}
                                            for use in uses],
                "content": [{"type": "text", "text": said}] * bool(said) + uses,
                "stop_reason": "tool_use" if uses else "end_turn"}


def _answers(monkeypatch, model: Scripted) -> None:
    """Whoever pays, the scripted model writes the reply."""
    from libs import assistant

    @contextmanager
    def paying_client(_store, _user_id, _payer):
        yield model, "anthropic"

    monkeypatch.setattr(assistant, "paying_client", paying_client)


def _env(monkeypatch) -> None:
    from libs.secrets import generate_key

    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    monkeypatch.setenv("SECRETS_KEK", f"k1:{generate_key()}")
    monkeypatch.setenv("PLATFORM_AI_PROVIDER", "gemini")
    monkeypatch.setenv("PLATFORM_AI_KEY", PLATFORM_KEY)


def _account(db):
    """One account with a search and the help chat open."""
    from libs import settings
    from libs.db import insert
    from libs.db.models import User, UserConfig

    user = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active"))
    insert(db, UserConfig(user_id=user.id, schema_version=2, config=copy.deepcopy(CONFIG)))
    settings.put(db, "support_chat_enabled", 1)
    return user


@pytest.fixture()
def api(db, monkeypatch):
    """The app on the test database, the reply written inside the request that asked."""
    from fastapi.testclient import TestClient

    from libs import assistant
    from libs.auth.access import issue_access_token
    from services.api.app import app

    _env(monkeypatch)
    user = _account(db)
    monkeypatch.setattr(assistant, "start", lambda reply_id: assistant.run(db, reply_id))

    with TestClient(app, base_url="https://testserver") as http:
        http.cookies.set("access_token", issue_access_token(user.id, uuid.uuid4()), path="/")
        yield SimpleNamespace(http=http, db=db, user=user)


def _ask(api, question: str = "hello") -> dict:
    asked = api.http.post("/support/messages", json={"text": question})
    assert asked.status_code == 202, asked.text
    return api.http.get("/support").json()["messages"][-1]


def _words(api) -> tuple[list[str], list[str]]:
    from libs.db import find_one
    from libs.db.models import UserConfig
    from libs.matching import green_words

    config = find_one(api.db, UserConfig, {"user_id": api.user.id}).config
    return green_words(config), config["sections"][0].get("exclude_keywords") or []


# ── the guard ─────────────────────────────────────────────────────────────────────────────────

def test_the_ai_can_only_offer_a_change_and_the_persons_tap_makes_it(api, monkeypatch):
    model = Scripted(("Let me add that.", [("add_word", {"word": "Python", "kind": "green"})]),
                     ("Tap the button to add python.", []))
    _answers(monkeypatch, model)

    reply = _ask(api, "please add python to my words")

    assert reply["steps"] == [{"label": "Add “python” to the words that find jobs", "output": None,
                               "error": False, "state": "proposed"}]
    assert "python" not in _words(api)[0], "the AI asked; nothing changes until the person taps"
    told = model.seen[1]["messages"][-1]["content"][0]["content"]
    assert "Nothing changes until they tap it" in told

    tapped = api.http.post(f"/support/replies/{reply['id']}/steps/0").json()
    assert (tapped["state"], tapped["output"]) == ("done", "Added.")
    assert "python" in _words(api)[0]
    again = api.http.post(f"/support/replies/{reply['id']}/steps/0").json()
    assert again["state"] == "done" and _words(api)[0].count("python") == 1, "one tap, one change"


def test_a_red_word_tapped_before_any_green_one_is_refused_and_says_why(api, monkeypatch):
    """A red word only hides what green words find: alone it would hide nothing and find nothing."""
    from libs.db.models import UserConfig

    api.db[UserConfig.COLLECTION].update_one({"user_id": api.user.id},
                                             {"$set": {"config.sections.0.keywords": []}})
    model = Scripted(("Let me hide those.", [("add_word", {"word": "unpaid", "kind": "red"})]),
                     ("Tap the button to hide unpaid jobs.", []))
    _answers(monkeypatch, model)

    reply = _ask(api, "hide unpaid jobs")
    tapped = api.http.post(f"/support/replies/{reply['id']}/steps/0").json()

    assert tapped["state"] == "failed" and "pick a word for the jobs you want first" in tapped["output"]
    assert "unpaid" not in _words(api)[1]


def test_a_button_is_only_what_was_offered_and_only_for_whoever_asked(api, monkeypatch):
    from libs.auth.access import issue_access_token
    from libs.db import insert
    from libs.db.models import User

    _answers(monkeypatch, Scripted(("", [("my_words", {}), ("remove_word", {"word": "Senior"})]),
                                   ("Tap the button to remove it.", [])))
    reply = _ask(api, "stop hiding senior jobs")
    assert [step["state"] for step in reply["steps"]] == [None, "proposed"]
    assert "senior" in reply["steps"][0]["output"], "the lookup read their real words"

    button = f"/support/replies/{reply['id']}/steps"
    assert api.http.post(f"{button}/0").status_code == 404, "a lookup is not a button"
    assert api.http.post(f"{button}/7").status_code == 404
    stranger = insert(api.db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active"))
    mine = api.http.cookies.get("access_token")
    api.http.cookies.set("access_token", issue_access_token(stranger.id, uuid.uuid4()), path="/")
    assert api.http.post(f"{button}/1").status_code == 404, "somebody else's button"
    assert api.http.get(f"/support/replies/{reply['id']}/stream").status_code == 404
    api.http.cookies.set("access_token", mine, path="/")
    assert "senior" in _words(api)[1]

    assert api.http.post(f"{button}/1").json()["state"] == "done"
    assert "senior" not in _words(api)[1]


# ── who pays ──────────────────────────────────────────────────────────────────────────────────

def test_a_platform_paid_answer_uses_one_request_and_an_own_key_none(api, monkeypatch):
    from libs.llm import ai_requests_today
    from libs.secrets import SecretStore

    model = Scripted(("Hello.", []), ("Hello again.", []))
    _answers(monkeypatch, model)

    _ask(api)
    assert ai_requests_today(api.db, api.user.id) == 1
    assert api.http.get("/support").json()["ai"] == {"payer": "platform", "provider": "Gemini",
                                                    "left": 19, "per_day": 20}
    assert model.seen[0]["model"] == "claude-haiku-4-5", "the operator's model for that provider"

    SecretStore(api.db).put(api.user.id, "openai_api", "sk-proj-" + "o" * 40)
    _ask(api)
    assert ai_requests_today(api.db, api.user.id) == 1, "their own key is not the allowance"
    assert api.http.get("/support").json()["ai"] == {"payer": "own", "provider": "OpenAI",
                                                    "left": None, "per_day": 20}


def test_with_no_ai_left_the_page_says_why_and_what_to_do(api, monkeypatch):
    from libs.limits import set_limit

    _answers(monkeypatch, Scripted(("Hello.", [])))
    monkeypatch.delenv("PLATFORM_AI_KEY")
    assert api.http.get("/support").json()["closed"] == (
        "This site cannot pay for AI answers on your account. Add your own Claude, Gemini or "
        "OpenAI key in Settings, and you can ask as much as you like.")

    monkeypatch.setenv("PLATFORM_AI_KEY", PLATFORM_KEY)
    set_limit(api.db, api.user.id, "ai_platform_requests_per_day", 1)
    _ask(api)

    page = api.http.get("/support").json()
    assert page["closed"] == ("You have used today's 1 AI requests. They come back at midnight. To "
                              "ask more now, add your own Claude, Gemini or OpenAI key in Settings.")
    assert page["ai"]["left"] == 0
    refused = api.http.post("/support/messages", json={"text": "one more"})
    assert (refused.status_code, refused.json()["detail"]) == (409, page["closed"])
    assert len(api.http.get("/support").json()["messages"]) == 2, "a refused question is not kept"


def test_the_help_chat_is_closed_until_the_operator_opens_it(api, monkeypatch):
    from libs import settings

    _answers(monkeypatch, Scripted(("Hello.", [])))
    settings.put(api.db, "support_chat_enabled", 0)

    assert api.http.get("/support").json()["closed"] == "The help chat is not open yet."
    assert api.http.post("/support/messages", json={"text": "hi"}).status_code == 409
    assert api.http.get("/support").json()["messages"] == []

    settings.put(api.db, "support_chat_enabled", 1)
    assert _ask(api)["text"] == "Hello."


# ── consent ───────────────────────────────────────────────────────────────────────────────────

def test_without_consent_what_their_documents_say_never_reaches_the_ai(api, monkeypatch):
    from libs.db.models import User

    api.db[User.COLLECTION].update_one({"_id": api.user.id}, {"$set": {"suggestions": {
        "green": [{"term": "kotlin", "reason": "Your job title at Zanzibar Widgets"}],
        "red": [], "dismissed": []}}})
    model = Scripted(("", [("suggested_words", {})]), ("I cannot see those.", []),
                     ("", [("suggested_words", {})]), ("We suggest kotlin.", []))
    _answers(monkeypatch, model)

    _ask(api, "which words do you suggest?")
    assert {"suggested_words", "stop_suggesting"}.isdisjoint(model.seen[0]["tools"])
    assert "Zanzibar" not in json.dumps(model.seen), "nothing their documents say reached the AI"
    assert "my_words" in model.seen[0]["tools"], "their words and the site's help still answer"

    version = api.http.get("/account/consent").json()["current_version"]
    assert api.http.post("/account/consent", json={"version": version}).status_code == 200
    _ask(api, "and now?")
    assert "suggested_words" in model.seen[2]["tools"]
    assert "Zanzibar" in json.dumps(model.seen[3]["messages"]), "with consent, it may"


# ── streamed, and restart-proof: real threads, the app's own database client ─────────────────

@pytest.fixture()
def live(db, monkeypatch):
    """The app as it runs: each reply written on a thread of its own, through `libs.db.database()`."""
    from fastapi.testclient import TestClient

    from libs.auth.access import issue_access_token
    from services.api.app import app

    _env(monkeypatch)
    user_id = _account(db).id

    @contextmanager
    def started():
        """The API starting: its start-up runs, as it does after a deploy or a crash."""
        with TestClient(app, base_url="https://testserver") as http:
            http.cookies.set("access_token", issue_access_token(user_id, uuid.uuid4()), path="/")
            yield http

    yield SimpleNamespace(db=db, user_id=user_id, started=started)


def _stream(http, reply_id: int, since: float) -> list[tuple[str, dict, float]]:
    """Every event of a reply's stream: its name, its data, and when it arrived."""
    events, name = [], ""
    with http.stream("GET", f"/support/replies/{reply_id}/stream") as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                events.append((name, json.loads(line[len("data: "):]), time.monotonic() - since))
    return events


def test_a_reply_streams_as_a_snapshot_then_deltas_and_steps_then_done(live, monkeypatch):
    answer = "Your green word is data engineer, and senior hides the jobs you do not want."
    _answers(monkeypatch, Scripted(("Let me look at your words.", [("my_words", {})]), (answer, []),
                                   delay=0.04))

    with live.started() as http:
        asked = time.monotonic()
        reply_id = http.post("/support/messages", json={"text": "what are my words?"}).json()["reply_id"]
        events = _stream(http, reply_id, asked)
        kept = http.get("/support").json()["messages"][-1]

    names = [name for name, _, _ in events]
    assert names[0] == "snapshot" and names[-1] == "done" and names.count("done") == 1, names
    assert names.count("delta") >= 3, f"the text arrives as it is written: {names}"
    step = next(data["step"] for name, data, _ in events if name == "step")
    assert step["label"] == "Looked at your words" and "data engineer" in step["output"]

    done = events[-1][1]
    whole = "Let me look at your words.\n\n" + answer
    assert done["status"] == "done" and done["text"] == whole
    assert events[0][1].get("text", "") + "".join(data["text"] for name, data, _ in events
                                                  if name == "delta") == whole
    first = next(at for name, data, at in events if data.get("text") and name != "done")
    assert first < events[-1][2] - 0.3, "the first words are on screen well before the last"
    assert (kept["status"], kept["text"], kept["steps"][0]["label"]) == ("done", whole,
                                                                       "Looked at your words")


def test_a_reply_cut_off_by_a_restart_is_written_once_when_the_api_starts_again(live, monkeypatch):
    from libs import assistant
    from libs.db import insert, now
    from libs.db.models import SetupConversation, SetupMessage

    gate = threading.Event()

    class Waits(Scripted):
        """Answers everything, counting each call as it starts; the first writer of a question
        with "wait" in it is held until the test lets it go."""
        writing, calls = threading.Event(), 0

        def complete_tools(self, **kwargs):
            self.calls += 1
            if "wait" in json.dumps(kwargs["messages"][-1]) and not self.writing.is_set():
                self.writing.set()
                assert gate.wait(10)
            return super().complete_tools(**kwargs)

    model = Waits(*[("Your words are fine.", [])] * 3)
    _answers(monkeypatch, model)
    chat = insert(live.db, SetupConversation(user_id=live.user_id, title="Help"))
    insert(live.db, SetupMessage(user_id=live.user_id, conversation_id=chat.id, role="user",
                                 content="are my words ok?", agent="support"))
    # Half written when the API stopped, by a writer whose lease has since run out.
    cut_off_id = insert(live.db, SetupMessage(
        user_id=live.user_id, conversation_id=chat.id, role="assistant", content="Your wor",
        agent="support", status="streaming", tool_calls={"payer": "platform", "steps": []},
        lease_until=now() - timedelta(seconds=1))).id

    def reply(http, reply_id: int) -> dict:
        for _ in range(100):
            found = next(m for m in http.get("/support").json()["messages"] if m["id"] == reply_id)
            if found["status"] not in ("pending", "streaming"):
                return found
            time.sleep(0.05)
        raise AssertionError(f"reply {reply_id} was never finished")

    with live.started() as http:
        assert reply(http, cut_off_id)["text"] == "Your words are fine.", "started again, from the top"
        assert model.seen[0]["messages"] == [{"role": "user", "content": "are my words ok?"}]

        # A reply a live worker is writing is not written again by a worker starting up now.
        held = http.post("/support/messages", json={"text": "wait for it"}).json()["reply_id"]
        assert model.writing.wait(5)
        assistant.start(held).result(5)
        gate.set()
        assert reply(http, held)["status"] == "done"
    assert model.calls == 2, "each reply was written once"


def test_a_reply_whose_writer_died_is_started_again_by_the_stream_once_its_lease_runs_out(
        live, monkeypatch):
    """A writer that dies mid-reply still holds its lease when the API starts, so the start-up leaves
    it alone — it might be alive on another worker. The page watching it must not wait forever: the
    stream starts it again once the lease has run out, and not before."""
    from libs.db import get, insert, now
    from libs.db.models import SetupConversation, SetupMessage

    model = Scripted(*[("Your words are fine.", [])] * 2)
    _answers(monkeypatch, model)
    chat = insert(live.db, SetupConversation(user_id=live.user_id, title="Help"))
    insert(live.db, SetupMessage(user_id=live.user_id, conversation_id=chat.id, role="user",
                                 content="are my words ok?", agent="support"))

    with live.started() as http:
        held_for = 1.0
        reply_id = insert(live.db, SetupMessage(
            user_id=live.user_id, conversation_id=chat.id, role="assistant", content="Your wor",
            agent="support", status="streaming", tool_calls={"payer": "platform", "steps": []},
            lease_until=now() + timedelta(seconds=held_for))).id
        opened = time.monotonic()
        events = _stream(http, reply_id, opened)

    assert events[0][:2] == ("snapshot", {"text": "Your wor", "status": "streaming", "steps": []})
    name, done, at = events[-1]
    assert name == "done" and (done["status"], done["text"]) == ("done", "Your words are fine."), events
    assert at >= held_for - 0.2, "a live lease was taken: the writer might still have been alive"
    assert len(model.seen) == 1, "written once, however often the stream looked"
    assert get(live.db, SetupMessage, reply_id).lease_until is None, "a finished reply holds no lease"


def test_a_writer_that_outlived_its_lease_cannot_write_over_the_one_that_took_over(db, user):
    """A slow model call with retries can outlast the lease; the reply then belongs to its new writer."""
    from libs import assistant
    from libs.db import insert, now
    from libs.db.models import SetupConversation, SetupMessage

    conversation = insert(db, SetupConversation(user_id=user.id))
    reply = insert(db, SetupMessage(user_id=user.id, conversation_id=conversation.id, role="assistant",
                                    content="the new writer's words", agent=assistant.SUPPORT,
                                    status="streaming", lease_until=now() + assistant.LEASE,
                                    lease_holder="the-new-writer"))
    reply.lease_holder = "the-old-writer"

    with pytest.raises(assistant.LeaseLost):
        assistant._Reply(db, reply)

    assert db.setup_messages.find_one({"_id": reply.id})["content"] == "the new writer's words"


def _pending(db, user_id, question: str = "are my words ok?") -> int:
    """A question and its reply, waiting for a writer."""
    from libs.db import insert
    from libs.db.models import SetupConversation, SetupMessage

    chat = insert(db, SetupConversation(user_id=user_id, title="Help"))
    insert(db, SetupMessage(user_id=user_id, conversation_id=chat.id, role="user", content=question,
                            agent="support"))
    return insert(db, SetupMessage(user_id=user_id, conversation_id=chat.id, role="assistant",
                                   content="", agent="support", status="pending",
                                   tool_calls={"payer": "platform", "steps": []})).id


def test_a_live_writer_keeps_its_reply_through_a_model_call_longer_than_the_lease(live, monkeypatch):
    """The lease is short, so a reply a restart cut off is taken over in seconds rather than after
    the longest a model call may take (it was 8 minutes). That is only safe because a live writer
    renews it while the model is silent: otherwise every slow answer would be written twice."""
    from libs import assistant
    from libs.db import get
    from libs.db.models import SetupMessage

    assert assistant.LEASE.total_seconds() <= 30, "a cut-off reply waits this long to be written again"
    monkeypatch.setattr(assistant, "LEASE", timedelta(seconds=0.6))

    class Slow(Scripted):
        def complete_tools(self, **kwargs):
            time.sleep(1.8)  # three leases with not a word written
            return super().complete_tools(**kwargs)

    model = Slow(*[("Your words are fine.", [])] * 2)
    _answers(monkeypatch, model)
    reply_id = _pending(live.db, live.user_id)

    writing = assistant.start(reply_id)
    time.sleep(1.2)
    assistant.run(live.db, reply_id)  # the page's stream, or a worker starting up, trying to take it
    writing.result(5)

    assert len(model.seen) == 1, "taken over from a writer that was alive"
    assert get(live.db, SetupMessage, reply_id).content == "Your words are fine."


def test_replies_beyond_the_writer_ceiling_wait_their_turn(live, monkeypatch):
    """Each reply used to get a thread of its own, however many were asked at once. Now one process
    writes at most `WRITERS` at a time and the rest stay `pending` until a writer is free."""
    from concurrent.futures import ThreadPoolExecutor

    from libs import assistant
    from libs.db import get
    from libs.db.models import SetupMessage

    gate, lock = threading.Event(), threading.Lock()

    class Held(Scripted):
        busy = most = 0

        def complete_tools(self, **kwargs):
            with lock:
                self.busy += 1
                self.most = max(self.most, self.busy)
            assert gate.wait(10)
            with lock:
                self.busy -= 1
            return super().complete_tools(**kwargs)

    model = Held(*[("Your words are fine.", [])] * 3)
    _answers(monkeypatch, model)
    monkeypatch.setattr(assistant, "_writers", ThreadPoolExecutor(max_workers=2))
    replies = [_pending(live.db, live.user_id, f"question {n}") for n in range(3)]

    writing = [assistant.start(reply_id) for reply_id in replies]
    for _ in range(100):
        if model.busy == 2:
            break
        time.sleep(0.05)
    time.sleep(0.2)
    assert (model.busy, get(live.db, SetupMessage, replies[2]).status) == (2, "pending")

    gate.set()
    for each in writing:
        each.result(5)
    assert model.most == 2, "more replies were written at once than there are writers"
    assert [get(live.db, SetupMessage, reply_id).status for reply_id in replies] == ["done"] * 3
