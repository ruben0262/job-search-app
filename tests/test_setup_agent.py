"""The setup conversation: what the agent can reach, and what it writes."""
from __future__ import annotations

import hashlib
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (no live Postgres)")


def _document(user_id, filename, kind, text):
    """One uploaded document."""
    from libs.db.models import UserDocument

    return UserDocument(user_id=user_id, filename=filename, kind=kind, chars=len(text), text=text,
                        text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest())


@pytest.fixture()
def account():
    """One user with two uploaded documents and a seeded set of scaffolds."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from libs.db.models import User
    from libs.profiles.repository import ProfileFiles

    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")

    user = User(email=f"{uuid.uuid4()}@example.test", password_hash="x", status="active")
    session.add(user)
    session.flush()
    session.add_all([_document(user.id, "cv.pdf", "cv",
                               "Alice Example — Backend Engineer at Northwind since 2021."),
                     _document(user.id, "history.txt", "history",
                               "I like building payment systems.")])
    ProfileFiles(session, user.id).seed()
    session.flush()

    try:
        yield session, user
    finally:
        session.close()
        trans.rollback()
        conn.close()


class FakeClient:
    """Answers with whatever turns the test queues, and records what it was asked."""

    def __init__(self, *turns):
        self.turns = list(turns)
        self.calls: list[dict] = []

    def complete_tools(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("on_text"):
            kwargs["on_text"]("…")
        turn = self.turns.pop(0) if self.turns else {"text": "Done.", "tool_uses": []}
        return {"content": [], "stop_reason": "end_turn", **turn}


def use(tool, **arguments):
    """One tool call, in the shape the adapter returns them."""
    return {"id": f"toolu_{tool}", "name": tool, "input": arguments}


# ── the tools ─────────────────────────────────────────────────────────────────────────────────

def test_the_agent_sees_the_documents_the_user_uploaded(account):
    from libs.agents.tools import SetupTools

    session, user = account
    tools = SetupTools(session, user.id)

    listing = tools.run("list_documents", {})
    assert "cv.pdf" in listing and "history.txt" in listing
    assert "Northwind" in tools.run("read_document", {"name": "cv.pdf"})


def test_an_account_with_no_documents_is_told_to_interview_instead(account):
    """Path C."""
    from libs.agents.tools import SetupTools
    from libs.db.models import UserDocument

    session, user = account
    session.query(UserDocument).filter(UserDocument.user_id == user.id).delete()
    session.flush()

    assert "Interview the user" in SetupTools(session, user.id).run("list_documents", {})


def test_a_missing_document_is_an_answer_not_an_exception(account):
    """A model that guessed a filename should be corrected and try again, not end the conversation."""
    from libs.agents.tools import SetupTools

    result = SetupTools(*account[::-1] if False else (account[0], account[1].id)).run(
        "read_document", {"name": "nope.pdf"})
    assert "No document called" in result


def test_writing_a_profile_file_marks_it_filled(account):
    from libs.agents.tools import SetupTools
    from libs.profiles.repository import ProfileFiles

    session, user = account
    tools = SetupTools(session, user.id)

    assert "Saved context.md" in tools.run(
        "write_profile_file", {"name": "context.md", "content": "# Alice Example\nReal profile."})

    files = ProfileFiles(session, user.id)
    assert files.read("context.md").startswith("# Alice Example")
    assert files.is_ready(), "a written context file should make the account generation-ready"


def test_a_file_nothing_reads_is_refused(account):
    """A model inventing `08-references.md` would write a section the user never sees again."""
    from libs.agents.tools import SetupTools

    session, user = account
    result = SetupTools(session, user.id).run(
        "write_profile_file", {"name": "08-references.md", "content": "..."})

    assert "not one of this platform's profile files" in result


def test_emptying_a_file_is_refused(account):
    from libs.agents.tools import SetupTools

    session, user = account
    assert "Refused" in SetupTools(session, user.id).run(
        "write_profile_file", {"name": "context.md", "content": "   "})


def test_the_written_rubric_reaches_the_column_the_labeller_reads(account):
    """Written here, read there — the family that cost this platform the most."""
    from libs.agents.tools import SetupTools
    from libs.scoring.rubric import profile_version

    session, user = account
    rubric = "# Rubric\nScore seniority first, then domain."
    SetupTools(session, user.id).run(
        "write_profile_file", {"name": "label.md", "content": rubric})
    session.flush()
    session.refresh(user)

    assert user.rubric_md == rubric, "the labeller reads the column, not the file"
    assert user.profile_version == profile_version(rubric)


def test_a_rewritten_rubric_only_stales_labels_when_it_actually_changed(account):
    """`profile_generation` is what marks existing labels stale."""
    from libs.agents.tools import SetupTools

    session, user = account
    tools = SetupTools(session, user.id)
    rubric = "# Rubric\nScore seniority first."

    tools.run("write_profile_file", {"name": "label.md", "content": rubric})
    session.flush()
    session.refresh(user)
    first = user.profile_generation

    # The same text again: a rewrite that changed nothing must cost nothing.
    tools.run("write_profile_file", {"name": "label.md", "content": rubric})
    session.flush()
    session.refresh(user)
    assert user.profile_generation == first

    tools.run("write_profile_file",
              {"name": "label.md", "content": rubric + "\nThen location."})
    session.flush()
    session.refresh(user)
    assert user.profile_generation == first + 1, "a changed rubric stales the labels under it"


def test_a_scoring_relevant_write_is_reported_but_not_acted_on(account):
    """The whole retraining policy: the tool says the rubric moved and stops there."""
    from libs.agents.tools import SetupTools

    session, user = account
    tools = SetupTools(session, user.id)

    tools.run("write_profile_file", {"name": "context.md", "content": "# Alice\nreal"})
    assert tools.scoring_edits == []

    tools.run("write_profile_file", {"name": "label.md", "content": "# Rubric\nreal anchors"})
    assert tools.scoring_edits == ["label.md"]


def test_the_tools_reach_one_account_only(account):
    """The executor is constructed with a user id and every query carries it."""
    from libs.agents.tools import SetupTools
    from libs.db.models import User

    session, user = account
    other = User(email=f"{uuid.uuid4()}@example.test", password_hash="x", status="active")
    session.add(other)
    session.flush()
    session.add(_document(other.id, "secret.pdf", "cv", "Somebody else's history."))
    session.flush()

    tools = SetupTools(session, user.id)
    assert "secret.pdf" not in tools.run("list_documents", {})
    assert "No document called" in tools.run("read_document", {"name": "secret.pdf"})


def _chat(session, user_id):
    """A conversation to talk in. Every turn belongs to one now — the unit of context."""
    from libs.db.models import SetupConversation

    row = SetupConversation(user_id=user_id)
    session.add(row)
    session.flush()
    return row.id


# ── the loop ──────────────────────────────────────────────────────────────────────────────────

def test_a_turn_runs_tools_then_answers(account):
    from libs.agents.loop import run_turn

    session, user = account
    chat = _chat(session, user.id)
    client = FakeClient(
        {"text": "Let me read your CV.", "tool_uses": [use("read_document", name="cv.pdf")]},
        {"text": "You are a backend engineer. Saving that.",
         "tool_uses": [use("write_profile_file", name="context.md", content="# Alice\nreal")]},
        {"text": "Done — your profile is set up.", "tool_uses": []},
    )

    result = run_turn(session, user.id, conversation_id=chat, client=client, message="Set me up")

    assert result["tools_run"] == ["read_document", "write_profile_file"]
    assert result["files_written"] == ["context.md"]
    assert "Done" in result["reply"]
    assert len(client.calls) == 3


def test_the_transcript_replays_tool_calls_so_the_model_sees_what_it_wrote(account):
    """The difference between the agent knowing what is in `context.md` and guessing."""
    from libs.agents.loop import history, run_turn

    session, user = account
    chat = _chat(session, user.id)
    run_turn(session, user.id, conversation_id=chat, client=FakeClient(
        {"text": "Reading.", "tool_uses": [use("read_document", name="cv.pdf")]},
        {"text": "Got it.", "tool_uses": []},
    ), message="go")

    replayed = history(session, user.id, chat)
    assert any(block.get("type") == "tool_use"
               for turn in replayed if isinstance(turn["content"], list)
               for block in turn["content"])
    assert any("Northwind" in str(turn["content"]) for turn in replayed)


def test_the_conversation_resumes_rather_than_restarting(account):
    """No session object holds the thread — a closed tab."""
    from libs.agents.loop import run_turn

    session, user = account
    chat = _chat(session, user.id)
    run_turn(session, user.id, conversation_id=chat, client=FakeClient({"text": "Hello.", "tool_uses": []}),
             message="hi")
    client = FakeClient({"text": "Still here.", "tool_uses": []})
    run_turn(session, user.id, conversation_id=chat, client=client, message="are you there")

    sent = client.calls[0]["messages"]
    assert [turn["role"] for turn in sent] == ["user", "assistant", "user"]
    assert sent[0]["content"] == "hi", "the second turn did not carry the first"
    assert sent[-1]["content"] == "are you there"


def test_a_runaway_turn_stops_and_says_so(account):
    """Everything written before the limit is already saved."""
    from libs.agents import MAX_TOOL_ITERATIONS
    from libs.agents.loop import ITERATION_LIMIT_MESSAGE, run_turn

    session, user = account
    chat = _chat(session, user.id)
    forever = FakeClient(*[{"text": "", "tool_uses": [use("list_documents")]}
                           for _ in range(MAX_TOOL_ITERATIONS + 5)])

    result = run_turn(session, user.id, conversation_id=chat, client=forever, message="go")

    assert ITERATION_LIMIT_MESSAGE in result["reply"]
    assert len(forever.calls) == MAX_TOOL_ITERATIONS


def test_an_unknown_command_is_refused_before_anything_is_sent(account):
    from libs.agents.loop import run_turn

    session, user = account
    chat = _chat(session, user.id)
    client = FakeClient()
    with pytest.raises(ValueError, match="unknown setup agent"):
        run_turn(session, user.id, conversation_id=chat, client=client, message="go",
                 agent="setup-everything")
    assert client.calls == []


def test_text_is_streamed_as_it_arrives(account):
    """A setup turn can run for minutes. Without this the page shows nothing until it ends."""
    from libs.agents.loop import run_turn

    session, user = account
    chat = _chat(session, user.id)
    chunks: list[str] = []
    run_turn(session, user.id, conversation_id=chat, client=FakeClient({"text": "Hello.", "tool_uses": []}),
             message="hi", on_text=chunks.append)

    assert chunks, "no text reached the caller while the turn was running"


def test_seeding_reports_what_it_actually_wrote(account):
    """`ON CONFLICT DO NOTHING` reports `rowcount = -1` through this driver."""
    from libs.db.models import User
    from libs.profiles import SEED_FILES
    from libs.profiles.repository import ProfileFiles

    session, _user = account
    fresh = User(email=f"{uuid.uuid4()}@example.test", password_hash="x", status="active")
    session.add(fresh)
    session.flush()

    files = ProfileFiles(session, fresh.id)
    assert files.seed() == len(SEED_FILES)
    # Re-seeding must neither write nor claim to have written.
    assert files.seed() == 0


# ── the role-angles command ───────────────────────────────────────────────────────────────────

def test_the_role_angles_command_is_loadable_and_names_nobody():
    """It is platform-authored rather than ported, and it is served to every account."""
    import re

    from libs.agents import command

    body = command("setup-role-angles")
    assert "08-role-angles.md" in body
    assert not re.search(r"\bRuben\b|\bLekli\b", body, re.IGNORECASE)


def test_the_role_angles_file_is_writeable_by_the_agent():
    """A command that cannot write the file it exists to write would fail at the last step."""
    from services.api.setup_routes import WRITEABLE

    assert "08-role-angles.md" in WRITEABLE


def test_the_role_angles_agent_is_one_the_api_will_accept():
    from libs.db.models import SETUP_AGENTS

    assert "setup-role-angles" in SETUP_AGENTS


def test_the_scaffold_says_one_angle_is_an_acceptable_answer():
    """The failure mode is invention."""
    from libs.agents import command
    from libs.profiles import scaffold

    assert "one angle is the honest answer" in scaffold("08-role-angles.md")
    assert "one angle is the honest answer" in command("setup-role-angles")


def test_writing_the_rubric_reaches_the_column_the_labeller_reads(account):
    """A finished rubric that nothing downstream could see."""
    from libs.agents.tools import SetupTools
    from libs.scoring.rubric import profile_version

    session, user = account
    tools = SetupTools(session, user.id)
    tools.run("write_profile_file", {"name": "label.md", "content": "score jobs like this"})
    session.flush()

    assert user.rubric_md == "score jobs like this"
    assert user.profile_version == profile_version("score jobs like this")
    # First write, so nothing was invalidated: there are no labels to make stale.
    assert user.profile_generation == 0


def test_rewriting_the_rubric_marks_existing_labels_stale(account):
    """`profile_generation` is what makes a label stale."""
    from libs.agents.tools import SetupTools

    session, user = account
    tools = SetupTools(session, user.id)
    tools.run("write_profile_file", {"name": "label.md", "content": "first rubric"})
    session.flush()
    assert user.profile_generation == 0

    tools.run("write_profile_file", {"name": "label.md", "content": "first rubric"})
    session.flush()
    assert user.profile_generation == 0, "an identical rewrite changes nothing"

    tools.run("write_profile_file", {"name": "label.md", "content": "a different rubric"})
    session.flush()
    assert user.profile_generation == 1


def test_every_turn_is_given_what_the_person_typed_without_the_model_asking(account):
    """**Injected into the system prompt, not offered as a tool, and this reads the real call site.**

    A tool the model *may* call is one it may skip — and then the hard data exists while the agent
    never looks. That is how one account's two extra links never reached the prompt that writes its
    contact line: `users.links` had three, the prompt had one, and nothing was wrong enough to error.

    Asserted against what `run_turn` actually handed the client, not by composing `system_prompt()`
    and `harddata.render()` here. Composing it in the test would pass with the injection deleted,
    which is the whole class of defect this session has been unpicking.
    """
    from libs.agents.loop import run_turn

    session, user = account
    user.full_name = "Alice Example"
    user.links = [{"url": "https://github.com/alice", "platform": "github"},
                  {"url": "https://alice.dev", "platform": "other"}]
    session.flush()

    chat = _chat(session, user.id)
    client = FakeClient({"text": "Noted.", "tool_uses": []})
    run_turn(session, user.id, conversation_id=chat, client=client, message="hello")

    system = client.calls[0]["system"]
    assert "Alice Example" in system, "the turn never saw the name they typed"
    assert "https://alice.dev" in system, "the turn never saw every link they typed"
    assert "typed themselves" in system, "nothing told the model which source is authoritative"
