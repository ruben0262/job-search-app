"""Driving one capture session, start to profile.

Four client mechanics that are easy to get wrong and silent when they are
(`PROFILE_CAPTURE_PLAN.md` §4.4), all of them tested:

1. **Stream first.** The session is created idle and the kickoff is sent *after* the stream is open.
   A session created with `initial_events` starts running immediately and a stream opened afterwards
   misses the opening events.
2. **Break correctly.** Exit on `session.status_terminated`, or on an idle whose `stop_reason` is
   anything other than `requires_action`. Breaking on plain idle stops mid-interview while the agent
   is waiting for the user.
3. **Cap it.** A turn ceiling and an event ceiling, enforced here. An interview that will not
   converge must fall into manual entry, not run forever on the user's credit.
4. **Never trust the output.** The profile is downloaded as a file and validated against the schema;
   anything malformed is a `CaptureFailed`, which the caller turns into manual entry.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from libs.contracts.profile import ProfileSchema
from services.capture.agent import (
    KICKOFF,
    PROFILE_FILENAME,
    agent_spec,
    document_resources,
    environment_spec,
    write_is_allowed,
)
from services.capture.client import CaptureClient, Event

logger = logging.getLogger(__name__)

# Both deliberately generous — capture is a real interview — but finite. The cost of an unbounded
# session is the user's money, and the failure mode is a spinner that never resolves.
MAX_USER_TURNS = 25
MAX_EVENTS = 400

# The listing lags the session going idle by a second or two.
OUTPUT_RETRIES = 3


class CaptureFailed(RuntimeError):
    """Capture did not produce a usable profile. Always recoverable — the caller falls back."""


@dataclass
class CaptureResult:
    profile: ProfileSchema
    agent_id: str
    session_id: str
    transcript: list[str] = field(default_factory=list)
    denied_writes: list[str] = field(default_factory=list)


def run_capture(
    client: CaptureClient,
    *,
    documents: dict[str, bytes],
    answer: Callable[[str], str | None],
    existing_agent_id: str | None = None,
    max_user_turns: int = MAX_USER_TURNS,
    max_events: int = MAX_EVENTS,
) -> CaptureResult:
    """Run the interview and return the (unconfirmed) profile.

    `answer` is how the product supplies the user's replies — given the agent's latest message, it
    returns their answer, or None to end the interview. Injected rather than assumed so the web
    flow, a CLI, and the tests all drive the same code.
    """
    if not documents:
        raise CaptureFailed("capture needs at least one document to read")

    environment_id = client.ensure_environment(environment_spec())
    agent_id = client.ensure_agent(agent_spec(), existing_agent_id)
    file_ids = {name: client.upload_document(name, data) for name, data in documents.items()}
    session_id = client.start_session(
        agent_id=agent_id, environment_id=environment_id,
        resources=document_resources(file_ids))

    result = CaptureResult(profile=None, agent_id=agent_id, session_id=session_id)  # type: ignore[arg-type]
    try:
        _converse(client, session_id, answer, result,
                  max_user_turns=max_user_turns, max_events=max_events)
        result.profile = _collect_profile(client, session_id)
    finally:
        # Archived whether or not it worked: a session left running is a container left running.
        try:
            client.archive_session(session_id)
        except Exception:
            # Cleanup must never mask the failure that caused it.
            logger.debug("archiving capture session %s failed", session_id, exc_info=True)
    return result


def _converse(client: CaptureClient, session_id: str, answer: Callable[[str], str | None],
              result: CaptureResult, *, max_user_turns: int, max_events: int) -> None:
    stream = client.stream_events(session_id)          # opened before anything is sent
    client.send_event(session_id, _user_message(KICKOFF))

    seen: set[str] = set()
    turns = events = 0

    for event in stream:
        events += 1
        if events > max_events:
            _give_up(client, session_id, f"exceeded {max_events} events")
        if event.id and event.id in seen:
            continue                                    # a reconnect replayed it
        if event.id:
            seen.add(event.id)

        if event.type == "agent.message" and event.text:
            result.transcript.append(event.text)

        elif event.type == "agent.tool_use" and event.needs_permission:
            _decide(client, session_id, event, result)

        elif event.type == "session.status_terminated":
            return

        elif event.type == "session.status_idle":
            if event.stop_reason != "requires_action":
                return                                  # the agent is done, not waiting
            reply = answer(result.transcript[-1] if result.transcript else "")
            if reply is None:
                _give_up(client, session_id, "the user ended the interview")
            turns += 1
            if turns > max_user_turns:
                _give_up(client, session_id, f"exceeded {max_user_turns} turns without converging")
            client.send_event(session_id, _user_message(reply))


def _decide(client: CaptureClient, session_id: str, event: Event, result: CaptureResult) -> None:
    """Approve a write only inside the outputs directory; deny anything else, with a reason."""
    allowed = event.tool_name != "write" or write_is_allowed(event.tool_input)
    decision: dict = {"type": "user.tool_confirmation", "tool_use_id": event.id,
                      "result": "allow" if allowed else "deny"}
    if not allowed:
        path = str(event.tool_input.get("path", ""))
        result.denied_writes.append(path)
        # Told why, so it corrects rather than retries. A silent denial reads to the agent as a
        # broken tool and it gives up on writing the profile at all.
        decision["deny_message"] = (
            f"writes are only permitted to {PROFILE_FILENAME} in the outputs directory")
    client.send_event(session_id, decision)


def _collect_profile(client: CaptureClient, session_id: str) -> ProfileSchema:
    raw = None
    for _ in range(OUTPUT_RETRIES):
        raw = client.download_output(session_id, PROFILE_FILENAME)
        if raw is not None:
            break
    if raw is None:
        raise CaptureFailed(f"the session produced no {PROFILE_FILENAME}")

    try:
        return ProfileSchema.model_validate(json.loads(raw))
    except Exception as exc:
        # Never partially accepted. A profile that validates in halves would put unreviewed agent
        # output behind a hard gate, which is the one thing the confirmation step exists to stop.
        raise CaptureFailed(f"the captured profile did not validate: {exc}") from exc


def _give_up(client: CaptureClient, session_id: str, why: str) -> None:
    client.send_event(session_id, {"type": "user.interrupt"})
    raise CaptureFailed(why)


def _user_message(text: str) -> dict:
    return {"type": "user.message", "content": [{"type": "text", "text": text}]}
