"""The narrow seam between capture and the Managed Agents beta.

Managed Agents is beta and sits on the onboarding critical path. Everything the SDK's shape can
change is confined to `AnthropicCaptureClient`; `session.py` talks only to this protocol. A contract
change breaks one adapter rather than the onboarding flow, and the tests drive the protocol with a
fake instead of the network.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol

# Pinned rather than tracked to latest: a beta header that moves under a running onboarding flow is
# a failure nobody sees until a user hits it.
MANAGED_AGENTS_BETA = "managed-agents-2026-04-01"
FILES_BETA = "files-api-2025-04-14"


@dataclass(frozen=True)
class Event:
    """One session event, flattened. The adapter owns the SDK's object shape; nothing else sees it."""

    id: str
    type: str
    text: str = ""
    tool_name: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    needs_permission: bool = False
    stop_reason: str = ""


class CaptureClient(Protocol):
    """Everything capture needs from Anthropic. Deliberately eight methods, not the whole SDK."""

    def ensure_environment(self, spec: dict) -> str: ...

    def ensure_agent(self, spec: dict, existing_id: str | None) -> str: ...

    def upload_document(self, filename: str, data: bytes) -> str: ...

    def start_session(self, *, agent_id: str, environment_id: str,
                      resources: list[dict]) -> str: ...

    def stream_events(self, session_id: str) -> Iterator[Event]: ...

    def send_event(self, session_id: str, event: dict) -> None: ...

    def download_output(self, session_id: str, filename: str) -> bytes | None: ...

    def archive_session(self, session_id: str) -> None: ...


class AnthropicCaptureClient:
    """`CaptureClient` over the real SDK, constructed with **the user's own API key**.

    Their onboarding, their spend, no platform cost and no abuse surface
    (`PROFILE_CAPTURE_PLAN.md` §4.1). It also means agent objects live in their workspace, which is
    why `ensure_agent` takes the id we stored for them rather than a platform-wide constant.
    """

    def __init__(self, api_key: str):
        # Imported here, not at module scope, so the orchestration and its tests run without the
        # beta SDK installed.
        import anthropic  # noqa: PLC0415

        self._client = anthropic.Anthropic(api_key=api_key)

    def ensure_environment(self, spec: dict) -> str:
        return self._client.beta.environments.create(**spec).id

    def ensure_agent(self, spec: dict, existing_id: str | None) -> str:
        if existing_id:
            return existing_id
        return self._client.beta.agents.create(**spec).id

    def upload_document(self, filename: str, data: bytes) -> str:
        return self._client.beta.files.upload(file=(filename, data)).id

    def start_session(self, *, agent_id: str, environment_id: str,
                      resources: list[dict]) -> str:
        # Created **without** `initial_events` on purpose. A session created with them starts
        # running immediately, so a stream opened afterwards misses the opening events. Creating
        # idle, opening the stream, then sending the kickoff is the only ordering that cannot lose
        # them (`PROFILE_CAPTURE_PLAN.md` §4.4).
        return self._client.beta.sessions.create(
            agent=agent_id, environment_id=environment_id, resources=resources).id

    def stream_events(self, session_id: str) -> Iterator[Event]:
        """Open the SSE connection **now**, iterate lazily.

        Not a generator, deliberately. A generator body does not run until the first `next()`, so
        `stream_events(...)` followed by `send_event(kickoff)` would open the connection *after* the
        kickoff — silently undoing the stream-first ordering the caller carefully arranged.
        """
        stream = self._client.beta.sessions.events.stream(session_id=session_id)
        handle = stream.__enter__()

        def _iterate() -> Iterator[Event]:
            try:
                for raw in handle:
                    yield _flatten(raw)
            finally:
                stream.__exit__(None, None, None)

        return _iterate()

    def send_event(self, session_id: str, event: dict) -> None:
        self._client.beta.sessions.events.send(session_id=session_id, events=[event])

    def download_output(self, session_id: str, filename: str) -> bytes | None:
        # Session-scoped listing needs the Managed Agents beta alongside the Files one; the SDK adds
        # only the Files header itself.
        for meta in self._client.beta.files.list(scope_id=session_id, betas=[MANAGED_AGENTS_BETA]):
            if meta.filename.endswith(filename):
                return self._client.beta.files.download(meta.id).read()
        return None

    def archive_session(self, session_id: str) -> None:
        self._client.beta.sessions.archive(session_id)


def _flatten(raw) -> Event:
    text = "".join(
        block.text for block in getattr(raw, "content", []) or []
        if getattr(block, "type", "") == "text")
    stop = getattr(raw, "stop_reason", None)
    return Event(
        id=getattr(raw, "id", "") or "",
        type=raw.type,
        text=text,
        tool_name=getattr(raw, "name", "") or "",
        tool_input=getattr(raw, "input", None) or {},
        needs_permission=getattr(raw, "evaluated_permission", None) == "ask",
        stop_reason=getattr(stop, "type", "") if stop is not None else "")
