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
