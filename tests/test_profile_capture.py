"""Profile capture: the orchestration, the containment, and the fallback.

Driven by a fake `CaptureClient` rather than the network — Managed Agents is beta, and the things
worth testing here are ordering, break conditions, caps, and what happens when the agent misbehaves.
None of those need Anthropic to be up, and all of them are silent when wrong.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest

pytest.importorskip("pydantic")
from pydantic import ValidationError

from libs.contracts.profile import ProfileSchema
from services.capture.agent import OUTPUTS_DIR, agent_spec, environment_spec, write_is_allowed
from services.capture.client import Event
from services.capture.manual import capture_or_manual, manual_profile
from services.capture.session import CaptureFailed, run_capture

VALID_PROFILE = {
    "identity": {"name": "Alice Example", "location": "Budapest"},
    "experience": [{"role": "Backend Engineer", "org": "Acme", "stack": ["Go", "Postgres"]}],
    "skills": {"languages": ["Go", "Python"]},
    "not_list": ["enterprise Java at depth", "mobile"],
}

DOCS = {"cv.pdf": b"%PDF-fake"}


def _idle(reason: str, eid: str = "e-idle") -> Event:
    return Event(id=eid, type="session.status_idle", stop_reason=reason)


def _msg(text: str, eid: str = "e-msg") -> Event:
    return Event(id=eid, type="agent.message", text=text)


def _write(path: str, eid: str = "e-write") -> Event:
    return Event(id=eid, type="agent.tool_use", tool_name="write",
                 tool_input={"path": path}, needs_permission=True)


class FakeClient:
    """Scripted Managed Agents. Each batch is delivered, then the caller's reply advances to the next.

    Batches rather than a flat list because that is what makes the ordering real: the generator only
    reaches batch N+1 after the caller has consumed batch N and responded to it.
    """

    def __init__(self, batches: list[list[Event]], output: bytes | None = None):
        self.batches = list(batches)
        self.output = output
        self.sent: list[dict] = []
        self.opened_after_sends: int | None = None
        self.archived: list[str] = []
        self.created_agents = 0
        self.uploads: list[str] = []

    def ensure_environment(self, spec):
        self.environment_spec = spec
        return "env_1"

    def ensure_agent(self, spec, existing_id):
        self.agent_spec = spec
        if existing_id:
            return existing_id
        self.created_agents += 1
        return "agent_new"

    def upload_document(self, filename, data):
        self.uploads.append(filename)
        return f"file_{filename}"

    def start_session(self, *, agent_id, environment_id, resources):
        self.resources = resources
        return "sesn_1"

    def stream_events(self, session_id):
        self.opened_after_sends = len(self.sent)

        def _iterate():
            while self.batches:
                yield from self.batches.pop(0)

        return _iterate()

    def send_event(self, session_id, event):
        self.sent.append(event)

    def download_output(self, session_id, filename):
        return self.output

    def archive_session(self, session_id):
        self.archived.append(session_id)


def _run(batches, output=None, answer=lambda _: "yes", **kw):
    output = json.dumps(VALID_PROFILE).encode() if output is None else output
    client = FakeClient(batches, output)
    return client, run_capture(client, documents=DOCS, answer=answer, **kw)


# ── ordering and lifecycle ────────────────────────────────────────────────────────────────────

def test_the_stream_is_open_before_the_kickoff_is_sent():
    """SSE has no replay. Sending first means the opening events are simply lost, and the symptom is
    an interview that appears to start mid-sentence."""
    client, _ = _run([[_msg("done"), _idle("end_turn")]])
    assert client.opened_after_sends == 0


def test_an_idle_awaiting_the_user_continues_the_interview():
    """The break condition that matters. Exiting on plain idle would end capture every time the
    agent asked a question — which is most of an interview."""
    answers = iter(["Budapest", "remote only"])
    client, result = _run(
        [[_msg("Where are you based?"), _idle("requires_action", "i1")],
         [_msg("Remote or hybrid?"), _idle("requires_action", "i2")],
         [_msg("Thanks, writing it up."), _idle("end_turn", "i3")]],
        answer=lambda _: next(answers))

    replies = [e for e in client.sent if e["type"] == "user.message"]
    assert [r["content"][0]["text"] for r in replies] == ["Read my documents and build my profile. "
                                                          "Ask me anything you cannot resolve from "
                                                          "them, then write the profile JSON.",
                                                          "Budapest", "remote only"]
    assert result.profile.identity.name == "Alice Example"


def test_a_terminated_session_ends_the_loop():
    client, _ = _run([[_msg("done"), Event(id="t", type="session.status_terminated")]])
    assert client.archived == ["sesn_1"]


def test_the_session_is_archived_even_when_capture_fails():
    """A session left running is a container left running, on the user's account."""
    client = FakeClient([[_idle("end_turn")]], output=None)
    with pytest.raises(CaptureFailed):
        run_capture(client, documents=DOCS, answer=lambda _: "x")
    assert client.archived == ["sesn_1"]


def test_repeated_event_ids_are_processed_once():
    """A reconnect replays history. Without dedup the same question is answered twice and the turn
    cap burns down against events the user never saw."""
    client, _ = _run([[_msg("Q?", "dup"), _idle("requires_action", "i1")],
                      [_msg("Q?", "dup"), _idle("end_turn", "i2")]])
    assert len([e for e in client.sent if e["type"] == "user.message"]) == 2  # kickoff + one answer


# ── caps: an interview that will not converge must fail, not run ──────────────────────────────

def test_the_turn_cap_ends_the_interview():
    client = FakeClient([[_msg(f"Q{i}?", f"m{i}"), _idle("requires_action", f"i{i}")]
                         for i in range(10)])
    with pytest.raises(CaptureFailed, match="converging"):
        run_capture(client, documents=DOCS, answer=lambda _: "yes", max_user_turns=3)
    assert {"type": "user.interrupt"} in client.sent


def test_the_event_cap_ends_the_interview():
    client = FakeClient([[_msg("chatter", f"m{i}") for i in range(50)]])
    with pytest.raises(CaptureFailed, match="events"):
        run_capture(client, documents=DOCS, answer=lambda _: "yes", max_events=10)


def test_the_user_can_end_the_interview():
    client = FakeClient([[_msg("Q?"), _idle("requires_action")]])
    with pytest.raises(CaptureFailed, match="ended"):
        run_capture(client, documents=DOCS, answer=lambda _: None)


def test_capture_without_documents_is_refused():
    """The interview's whole value is reading what the user actually did. With nothing to read it
    would produce a self-reported profile while looking like a captured one."""
    with pytest.raises(CaptureFailed, match="document"):
        run_capture(FakeClient([]), documents={}, answer=lambda _: "x")


# ── containment ───────────────────────────────────────────────────────────────────────────────

def test_a_write_to_the_outputs_directory_is_approved():
    client, _ = _run([[_write(f"{OUTPUTS_DIR}/profile.json"), _idle("end_turn")]])
    (decision,) = [e for e in client.sent if e["type"] == "user.tool_confirmation"]
    assert decision["result"] == "allow"


@pytest.mark.parametrize("path", [
    "/workspace/docs/cv.pdf",              # overwriting the user's own document
    f"{OUTPUTS_DIR}/../../etc/passwd",     # traversal
    f"{OUTPUTS_DIR}/nested/profile.json",  # outside the flat outputs directory
    "/etc/hosts",
])
def test_a_write_anywhere_else_is_denied_with_a_reason(path):
    """The prompt says where to write; the approval gate is what enforces it. A denial without a
    reason reads to the agent as a broken tool, and it stops trying to write the profile at all."""
    client, _ = _run([[_write(path), _idle("end_turn")]])
    (decision,) = [e for e in client.sent if e["type"] == "user.tool_confirmation"]
    assert decision["result"] == "deny"
    assert decision["deny_message"]


def test_the_dangerous_tools_are_off():
    """Capture reads documents and writes one file. `bash`, `edit`, `web_search` and `web_fetch`
    would each turn an injected instruction in a CV into something that can act."""
    (toolset,) = agent_spec()["tools"]
    assert toolset["default_config"]["enabled"] is False
    enabled = {c["name"] for c in toolset["configs"] if c["enabled"]}
    assert enabled == {"read", "glob", "grep", "write"}
    (write,) = [c for c in toolset["configs"] if c["name"] == "write"]
    assert write["permission_policy"] == {"type": "always_ask"}


def test_the_environment_has_no_egress():
    """Capture makes no outbound calls, so the allowlist stays empty rather than permissive."""
    networking = environment_spec()["config"]["networking"]
    assert networking["type"] == "limited"
    assert not networking.get("allowed_hosts")
    assert not networking.get("allow_package_managers")


def test_documents_are_mounted_under_one_directory():
    client, _ = _run([[_idle("end_turn")]])
    assert all(r["mount_path"].startswith("/workspace/docs/") for r in client.resources)
    assert all(r["type"] == "file" for r in client.resources)


# ── the output is never trusted ───────────────────────────────────────────────────────────────

def test_a_missing_profile_file_is_a_failure_not_an_empty_profile():
    client = FakeClient([[_idle("end_turn")]], output=None)
    with pytest.raises(CaptureFailed, match=r"profile\.json"):
        run_capture(client, documents=DOCS, answer=lambda _: "x")


@pytest.mark.parametrize("bad", [
    b"not json at all",
    b'{"identity": {}}',                                    # name is required
    b'{"identity": {"name": "A"}, "invented_field": 1}',    # extra="forbid"
    b'{"identity": {"name": "A"}, "not_list": "mobile"}',   # wrong type
])
def test_a_malformed_profile_is_rejected_whole(bad):
    """Never partially accepted. A profile that validated in halves would put unreviewed agent
    output behind a hard gate, which is exactly what the confirmation step exists to prevent."""
    client = FakeClient([[_idle("end_turn")]], output=bad)
    with pytest.raises(CaptureFailed, match=r"validate|profile"):
        run_capture(client, documents=DOCS, answer=lambda _: "x")


def test_a_captured_profile_arrives_unconfirmed():
    """The injection boundary. An uploaded CV is untrusted text, and nothing the agent wrote may
    reach a hard gate without a human click — so `confirmed` starts False regardless of what the
    agent put in the file."""
    payload = dict(VALID_PROFILE) | {"confirmed": True}
    client = FakeClient([[_idle("end_turn")]], json.dumps(payload).encode())
    result = run_capture(client, documents=DOCS, answer=lambda _: "x")
    assert result.profile.confirmed is True, "the schema records what was sent"

    _, plain = _run([[_idle("end_turn")]])
    assert plain.profile.confirmed is False, "and defaults to unconfirmed when it is not"


# ── the agent object is per user, created once ────────────────────────────────────────────────

def test_an_existing_agent_is_reused():
    """Agent objects are created with the user's key, so they live in the user's workspace — one per
    user, reused on re-capture. Creating one per session accumulates orphans in someone else's
    account."""
    client, result = _run([[_idle("end_turn")]], existing_agent_id="agent_stored")
    assert client.created_agents == 0
    assert result.agent_id == "agent_stored"


def test_the_first_capture_creates_the_agent():
    client, result = _run([[_idle("end_turn")]])
    assert client.created_agents == 1
    assert result.agent_id == "agent_new"


# ── manual entry, the fallback that must always exist ─────────────────────────────────────────

def test_manual_entry_uses_the_same_schema_as_capture():
    """One schema, so a hand-typed profile cannot be shaped differently and break the renderer six
    steps later."""
    profile = manual_profile(VALID_PROFILE)
    assert isinstance(profile, ProfileSchema)
    with pytest.raises(ValidationError):
        manual_profile({"identity": {}})


def test_a_failed_capture_falls_back_instead_of_blocking_onboarding():
    """Managed Agents is beta and on the critical path. A user whose capture fails must still be
    able to finish signing up."""
    def fails():
        raise CaptureFailed("the agent never wrote a profile")

    profile, source = capture_or_manual(fails, VALID_PROFILE)
    assert source == "manual"
    assert profile.identity.name == "Alice Example"


def test_a_successful_capture_is_not_replaced_by_the_fallback():
    client = FakeClient([[_idle("end_turn")]], json.dumps(VALID_PROFILE).encode())
    profile, source = capture_or_manual(
        lambda: run_capture(client, documents=DOCS, answer=lambda _: "x"), {})
    assert source == "capture"
    assert profile.identity.name == "Alice Example"


# ── the schema itself ─────────────────────────────────────────────────────────────────────────

def test_the_not_list_deduplicates_case_insensitively():
    """Each entry becomes a hard gate; duplicates would be rendered twice into the rubric."""
    profile = manual_profile(VALID_PROFILE | {"not_list": ["Mobile", "mobile", "MOBILE"]})
    assert profile.not_list == ["Mobile"]


def test_dates_are_free_text_not_parsed():
    """CVs say "2019", "Mar 2019", "present". Parsing them would reject real profiles to gain a
    precision nothing downstream uses."""
    profile = manual_profile(VALID_PROFILE | {
        "experience": [{"role": "Dev", "org": "Acme", "start": "Mar 2019", "end": "present"}]})
    assert profile.experience[0].end == "present"


@pytest.mark.parametrize("path,ok", [
    (f"{OUTPUTS_DIR}/profile.json", True),
    (f"{OUTPUTS_DIR}/notes.txt", True),
    (f"{OUTPUTS_DIR}", False),
    ("relative/profile.json", False),
])
def test_the_write_gate_in_isolation(path, ok):
    assert write_is_allowed({"path": path}) is ok


# ── persistence: the confirmation boundary is enforced at the write ───────────────────────────

@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="no live Postgres")
def test_an_unconfirmed_profile_cannot_be_stored():
    """The injection boundary again, this time where it counts. If an unconfirmed profile could
    reach `users.profile`, everything downstream would render from agent output nobody reviewed."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from libs.db.models import User
    from libs.tenant.repository import TenantRepository

    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    s = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        user = User(email=f"{uuid.uuid4()}@example.test", password_hash="x")
        s.add(user)
        s.flush()
        repo = TenantRepository(s)

        with pytest.raises(ValueError, match="unconfirmed"):
            repo.save_profile(user.id, manual_profile(VALID_PROFILE).model_dump())

        confirmed = manual_profile(VALID_PROFILE | {"confirmed": True}).model_dump()
        assert repo.save_profile(user.id, confirmed) == 1
        s.flush()
        assert repo.profile(user.id)["identity"]["name"] == "Alice Example"

        # Every artifact is built against a generation, so re-confirming must invalidate them.
        assert repo.save_profile(user.id, confirmed) == 2
    finally:
        s.close()
        trans.rollback()
        conn.close()
