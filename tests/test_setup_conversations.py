"""Several chats per account, each with its own context, capped at three.

The two properties that matter, and they pull in opposite directions:

- **a chat remembers**, across a closed tab, a sign-out, and a week away — the transcript is a
  table, so there is nothing to lose;
- **a chat forgets everything said in the other chats**, which is the whole reason for having more
  than one.

What crosses between them is the profile the tools write, not the talking.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("fastapi")

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (no live Postgres)")


@pytest.fixture()
def api():
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    os.environ.setdefault("SECRETS_KEK", "k1:" + "A" * 43 + "=")
    os.environ["DISABLE_RATE_LIMIT"] = "1"

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from libs.auth.access import issue_access_token
    from libs.db.models import User
    from services.api import deps
    from services.api.app import app

    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")

    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active")
    session.add(user)
    session.flush()

    app.dependency_overrides[deps.get_db] = lambda: session
    try:
        with TestClient(app, base_url="https://testserver") as http:
            http.cookies.set("access_token", issue_access_token(user.id, uuid.uuid4()), path="/")
            yield http, session, user
    finally:
        app.dependency_overrides.clear()
        session.close()
        trans.rollback()
        conn.close()
        os.environ.pop("DISABLE_RATE_LIMIT", None)


def _say(session, user_id, conversation_id, role, text):
    """Put a turn in a conversation without spending a model call."""
    from libs.db.models import SetupMessage

    session.add(SetupMessage(user_id=user_id, conversation_id=conversation_id,
                             role=role, content=text, agent="setup"))
    session.flush()


def _with_claude(session, user):
    """A real stored credential — the secret store wraps a per-user key, so a hand-written row is
    a secret nothing can decrypt."""
    from libs.secrets import SecretStore

    SecretStore(session).put(user.id, "claude_api", "sk-ant-api03-" + "x" * 40)
    session.flush()


def test_opening_the_page_lands_in_a_conversation_without_being_asked(api):
    """A new account should be able to start typing, not press New chat first."""
    http, _, _ = api
    body = http.get("/setup").json()

    assert body["conversation_id"]
    assert len(body["conversations"]) == 1
    assert body["messages"] == []
    assert body["max_conversations"] == 3


def test_a_conversation_survives_signing_out(api):
    """The transcript is a table, so there is nothing for a closed tab to lose."""
    http, session, user = api
    first = http.get("/setup").json()["conversation_id"]
    _say(session, user.id, uuid.UUID(first), "user", "I am a backend engineer.")
    _say(session, user.id, uuid.UUID(first), "assistant", "Noted.")

    # A brand new client with a fresh cookie — the same account signing back in.
    http.cookies.clear()
    from libs.auth.access import issue_access_token
    http.cookies.set("access_token", issue_access_token(user.id, uuid.uuid4()), path="/")

    body = http.get("/setup").json()
    assert body["conversation_id"] == first
    assert [m["content"] for m in body["messages"]] == ["I am a backend engineer.", "Noted."]


def test_a_new_chat_starts_empty_and_the_old_one_keeps_its_transcript(api):
    """The point of more than one: a fresh chat is fresh, and nothing is lost to make it so."""
    http, session, user = api
    first = http.get("/setup").json()["conversation_id"]
    _say(session, user.id, uuid.UUID(first), "user", "Six weeks of profile interview.")

    second = http.post("/setup/conversations").json()["id"]
    assert second != first

    fresh = http.get(f"/setup?conversation_id={second}").json()
    assert fresh["messages"] == []
    assert len(fresh["conversations"]) == 2

    old = http.get(f"/setup?conversation_id={first}").json()
    assert [m["content"] for m in old["messages"]] == ["Six weeks of profile interview."]


def test_the_model_is_never_shown_another_chats_transcript(api):
    """The context boundary, asserted where it is enforced rather than through the route."""
    from libs.agents.loop import history

    http, session, user = api
    first = uuid.UUID(http.get("/setup").json()["conversation_id"])
    second = uuid.UUID(http.post("/setup/conversations").json()["id"])

    _say(session, user.id, first, "user", "Secret from the first chat.")
    _say(session, user.id, second, "user", "Only this one.")

    assert [m["content"] for m in history(session, user.id, second)] == ["Only this one."]
    assert [m["content"] for m in history(session, user.id, first)] == ["Secret from the first chat."]


def test_three_chats_is_the_ceiling_and_the_refusal_says_what_to_do(api):
    """Fixed, and not a user setting: every turn replays its whole transcript on the user's own
    credential, so unbounded chats is unbounded spend on re-reading them."""
    http, _, _ = api
    http.get("/setup")  # the first one
    assert http.post("/setup/conversations").status_code == 201
    assert http.post("/setup/conversations").status_code == 201

    refused = http.post("/setup/conversations")
    assert refused.status_code == 409
    assert "Delete one" in refused.json()["detail"]
    assert len(http.get("/setup").json()["conversations"]) == 3


def test_deleting_a_chat_makes_room_and_leaves_the_profile_alone(api):
    """Deleting is how you make room — and the files are what the conversation was *for*."""
    from sqlalchemy import select

    from libs.db.models import UserProfileFile

    http, session, user = api
    first = http.get("/setup").json()["conversation_id"]
    http.post("/setup/conversations")
    http.post("/setup/conversations")
    _say(session, user.id, uuid.UUID(first), "user", "something said")

    files_before = session.scalars(
        select(UserProfileFile).where(UserProfileFile.user_id == user.id)).all()
    assert files_before, "the fixture should have seeded profile files"

    assert http.delete(f"/setup/conversations/{first}").status_code == 200
    session.flush()

    assert len(http.get("/setup").json()["conversations"]) == 2
    assert http.post("/setup/conversations").status_code == 201

    files_after = session.scalars(
        select(UserProfileFile).where(UserProfileFile.user_id == user.id)).all()
    assert len(files_after) == len(files_before), "deleting a chat must not touch the profile"


def test_deleting_the_last_chat_leaves_the_page_usable(api):
    """The next visit opens a fresh one, which is where a new account starts anyway."""
    http, session, _ = api
    only = http.get("/setup").json()["conversation_id"]
    http.delete(f"/setup/conversations/{only}")
    session.flush()

    body = http.get("/setup").json()
    assert body["conversation_id"] != only
    assert body["messages"] == []


def test_another_accounts_conversation_is_not_reachable(api):
    """A guessed id must be useless, so ownership is checked rather than assumed."""
    from libs.db.models import SetupConversation, User

    http, session, _ = api
    stranger = User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active")
    session.add(stranger)
    session.flush()
    theirs = SetupConversation(user_id=stranger.id)
    session.add(theirs)
    session.flush()

    assert http.get(f"/setup?conversation_id={theirs.id}").status_code == 404
    assert http.delete(f"/setup/conversations/{theirs.id}").status_code == 404
    assert http.post("/setup/message",
                     json={"message": "hello", "conversation_id": str(theirs.id)}).status_code == 404


# ── a turn that outlives the request ──────────────────────────────────────────────────────────

def test_sending_queues_a_turn_and_returns_at_once(api):
    """**The bug this replaced:** the turn ran inside the request, so switching tabs, refreshing or
    signing out aborted it — the question was committed, the answer never came, and nothing said
    why. Now the request only accepts the turn."""
    from libs.db.models import QueueJob

    http, session, user = api
    _with_claude(session, user)

    response = http.post("/setup/message", json={"message": "Set me up"})
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"

    # Both rows exist before any model call: the page renders the exchange immediately.
    messages = http.get("/setup").json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "Set me up"
    assert messages[1]["status"] == "pending", "the reply is a thinking state, not a missing row"

    queued = session.scalars(select_queue(QueueJob, user.id)).all()
    assert [job.lane for job in queued] == ["setup"]
    assert queued[0].payload["message_id"] == body["message_id"]


def select_queue(model, user_id):
    from sqlalchemy import select

    return select(model).where(model.user_id == user_id, model.lane == "setup")


def test_a_queued_turn_is_still_there_after_signing_out(api):
    """The point of moving it off the request path: the browser can do anything it likes."""
    from libs.auth.access import issue_access_token

    http, session, user = api
    _with_claude(session, user)
    http.post("/setup/message", json={"message": "Tell me about yourself"})

    http.cookies.clear()
    http.cookies.set("access_token", issue_access_token(user.id, uuid.uuid4()), path="/")

    messages = http.get("/setup").json()["messages"]
    assert messages[0]["content"] == "Tell me about yourself"
    assert messages[1]["status"] == "pending"


def test_a_turn_without_a_claude_credential_is_refused_at_the_form(api):
    """Answered where the user pressed send, rather than as a failed turn they find later."""
    http, _, _ = api
    refused = http.post("/setup/message", json={"message": "hello"})
    assert refused.status_code == 400
    assert "Connect your Claude account" in refused.json()["detail"]
    assert http.get("/setup").json()["messages"] == [], "nothing is written for a refused turn"


def test_the_worker_streams_into_the_pending_row_and_marks_it_done(api):
    """What the page polls. The row it watches is the row the finished reply lands in."""
    from libs.db.models import QueueJob, SetupMessage
    from services.setupagent.__main__ import handle

    http, session, user = api
    _with_claude(session, user)
    reply_id = http.post("/setup/message", json={"message": "hi"}).json()["message_id"]

    class _Client:
        def complete_tools(self, *, on_text=None, **kw):
            for chunk in ("Hello ", "there."):
                on_text(chunk)
            return {"text": "Hello there.", "tool_uses": [], "content": [], "stop_reason": "end_turn"}

    import services.setupagent.__main__ as worker

    original = worker.client_for
    worker.client_for = lambda value, kind: _Client()
    try:
        job = session.scalars(select_queue(QueueJob, user.id)).one()
        handle(session, job)
    finally:
        worker.client_for = original

    row = session.get(SetupMessage, reply_id)
    assert row.status == "done"
    assert row.content == "Hello there."


def test_a_retry_after_the_turn_finished_does_not_run_it_again(api):
    """At-least-once delivery is normal; spending the user's credential twice for one question
    is not."""
    from libs.db.models import QueueJob, SetupMessage
    from services.setupagent.__main__ import handle

    http, session, user = api
    _with_claude(session, user)
    reply_id = http.post("/setup/message", json={"message": "hi"}).json()["message_id"]

    row = session.get(SetupMessage, reply_id)
    row.status, row.content = "done", "already answered"
    session.flush()

    import services.setupagent.__main__ as worker

    called = []
    worker.client_for = lambda value, kind: called.append(1)
    job = session.scalars(select_queue(QueueJob, user.id)).one()
    handle(session, job)

    assert called == [], "a finished turn is not re-run"
    assert session.get(SetupMessage, reply_id).content == "already answered"


def test_setup_progress_names_the_command_that_produces_each_missing_file(api):
    """**The definition of done belongs to the code, not to an agent's opinion of itself.**

    Two failures this pins, both found on 2026-07-31. The chat sent no command name, so it always
    ran `setup` — which writes five of the eight required files. `context.md`, `08-role-angles.md`
    and `label.md` belong to commands nothing could start, so a new account could not finish setup
    and therefore could never train. And an agent asked to judge its own completeness will either
    stop early or polish forever; the ported director's gates read paths (`pipeline/config.yaml`,
    `scorer/models/scorer.joblib`) that do not exist on this platform at all.

    So progress is answered from the database, and every missing file names the command that
    produces it.
    """
    from libs.profiles import setup_files
    from libs.profiles.repository import ProfileFiles

    http, session, user = api
    http.get("/setup")  # seeds the scaffolds
    session.flush()

    body = http.get("/setup/progress").json()
    assert body["complete"] is False
    assert body["may_train"] is False
    assert set(body["missing"]) == set(setup_files())
    assert body["next_command"] == "setup-context", (
        "the first command is the one everything else reads — `setup-role-angles` says outright "
        "that context.md must already exist")

    # Every outstanding file is attributed to a command a user can actually start.
    for command, names in body["outstanding_by_command"].items():
        assert command in body["commands"], f"{names} belong to a command nothing can start"

    files = ProfileFiles(session, user.id)
    for name in setup_files():
        files.write(name, f"# {name}\n\nReal content.")
    session.flush()

    done = http.get("/setup/progress").json()
    assert done["complete"] is True
    assert done["may_train"] is True
    assert done["missing"] == [] and done["next_command"] is None


# ── stopping a turn ───────────────────────────────────────────────────────────────────────────

def test_stopping_a_turn_asks_rather_than_kills(api):
    """**A stop is a request, not a kill.**

    The turn runs on a worker that is usually mid-call to Anthropic, and that call cannot be
    interrupted without abandoning a response already paid for. So the API flips the row to
    `cancelling` and the worker notices at its next safe seam. Claiming it had stopped while it was
    still writing files would be a lie the file panel would immediately contradict.
    """
    from libs.db.models import SetupMessage

    http, session, user = api
    _with_claude(session, user)
    reply_id = http.post("/setup/message", json={"message": "hi"}).json()["message_id"]

    body = http.post(f"/setup/message/{reply_id}/stop").json()
    assert body["status"] == "cancelling"
    assert session.get(SetupMessage, reply_id).status == "cancelling"


def test_stopping_a_finished_turn_is_not_an_error(api):
    """A stop arriving a second after the reply landed is the normal race. Answering it with a 409
    would put a red banner on a screen where nothing went wrong."""
    from libs.db.models import SetupMessage

    http, session, user = api
    _with_claude(session, user)
    reply_id = http.post("/setup/message", json={"message": "hi"}).json()["message_id"]
    row = session.get(SetupMessage, reply_id)
    row.status = "done"
    session.flush()

    response = http.post(f"/setup/message/{reply_id}/stop")
    assert response.status_code == 200
    assert response.json()["status"] == "done"
    assert session.get(SetupMessage, reply_id).status == "done", "a finished turn is not reopened"


def test_another_users_turn_cannot_be_stopped(api):
    """Ownership is checked on the row, not inferred from the id being hard to guess."""
    from libs.db.models import SetupConversation, SetupMessage, User

    http, session, _ = api
    stranger = User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active")
    session.add(stranger)
    session.flush()
    chat = SetupConversation(user_id=stranger.id)
    session.add(chat)
    session.flush()
    theirs = SetupMessage(user_id=stranger.id, conversation_id=chat.id, role="assistant",
                          content="", agent="setup", status="pending")
    session.add(theirs)
    session.flush()

    assert http.post(f"/setup/message/{theirs.id}/stop").status_code == 404
    assert session.get(SetupMessage, theirs.id).status == "pending"


def test_a_stopped_turn_keeps_what_it_had_already_written(api):
    """**A stop is not a rollback.** Files the turn wrote before the stop are the user's — they were
    written on their credential and are already in the profile — so the row keeps its text and its
    tool calls, and lands in `cancelled` rather than `failed`: nothing went wrong."""
    from sqlalchemy import text

    from libs.agents.loop import run_turn
    from libs.db.models import SetupMessage

    http, session, user = api
    _with_claude(session, user)
    conversation_id = http.get("/setup").json()["conversation_id"]

    reply = SetupMessage(user_id=user.id, conversation_id=uuid.UUID(conversation_id),
                         role="assistant", content="", agent="setup", status="pending")
    session.add(reply)
    session.flush()

    class StopsAfterOneTool:
        """Writes a file, and the user presses stop while that call is still in flight — which is
        when somebody actually presses it, having seen the reply start."""

        def __init__(self):
            self.calls = 0

        def complete_tools(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                # The stop request arrives mid-call, from the API, in its own transaction.
                session.execute(
                    text("UPDATE setup_messages SET status = 'cancelling' WHERE id = :i")
                    .bindparams(i=reply.id))
                return {"text": "Writing your profile.",
                        "content": [{"type": "text", "text": "Writing your profile."}],
                        "tool_uses": [{"id": "t1", "name": "write_profile_file",
                                       "input": {"name": "context.md",
                                                 "content": "# Context\n\nReal content."}}]}
            raise AssertionError("the loop called the model again after the stop")

    result = run_turn(session, user.id, client=StopsAfterOneTool(), message="hi",
                      conversation_id=uuid.UUID(conversation_id), reply_row=reply)

    assert result["cancelled"] is True
    assert session.get(SetupMessage, reply.id).status == "cancelled"
    assert "context.md" in result["files_written"], "work done before the stop is kept"


def test_profile_files_are_refused_when_the_operator_closes_them(api):
    """**The setting has to bite on the endpoint, not only on the page.**

    Hiding the tiles while this still returned the contents would be a curtain: the URL is
    guessable, the response is JSON, and anyone with the page already open would keep reading it.
    """
    from libs import settings
    from libs.profiles.repository import ProfileFiles

    http, session, user = api
    ProfileFiles(session, user.id).write("context.md", "a private fact about this person",
                                         kind="profile")
    session.flush()
    assert http.get("/setup/files/context.md").status_code == 200

    settings.put(session, "profile_files_visible", False)
    session.flush()

    closed = http.get("/setup/files/context.md")
    assert closed.status_code == 403
    assert "a private fact about this person" not in closed.text
    # Editing implies reading -- the form is filled with the contents before it is saved.
    assert http.put("/setup/files/context.md", json={"content": "edited"}).status_code == 403
    # And the page stops offering what it cannot open.
    assert http.get("/setup").json()["files_visible"] is False
