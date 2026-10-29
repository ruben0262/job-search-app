"""The Anthropic adapter, and the shape every provider's adapter shares (`LlmClient`)."""
from __future__ import annotations

import json
import logging
from typing import Protocol

logger = logging.getLogger(__name__)

# How many times the SDK retries a 429 or a 5xx before giving up.
MAX_RETRIES = 2

# The identity block a subscription OAuth token is entitled to.
CLAUDE_CODE_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."

# An OAuth token authenticates on `Authorization: Bearer` and needs this header; an API key does not.
CLAUDE_OAUTH_BETA = "oauth-2025-04-20"


# Either satisfies the Claude requirement (D2).
CLAUDE_KINDS = ("claude_api", "claude_oauth")


class LlmError(RuntimeError):
    """A call did not produce usable output. Callers decide whether that is fatal."""


class LlmClient(Protocol):
    def complete_json(self, *, model: str, system: str, messages: list[dict],
                      schema: dict, max_tokens: int,
                      thinking_tokens: int | None = None) -> dict: ...

    def complete_text(self, *, model: str, system: str, messages: list[dict],
                      max_tokens: int, thinking_tokens: int | None = None) -> str: ...

    def complete_tools(self, *, model: str, system: str, messages: list[dict], tools: list[dict],
                       max_tokens: int, on_text=None) -> dict: ...


class AnthropicClient:
    """`LlmClient` over the real SDK, built from the user's own credential."""

    def __init__(self, value: str, kind: str = "claude_api"):
        import anthropic  # noqa: PLC0415 - keeps the SDK off the import path of everything else

        # Eight retries, not the SDK's two.
        self._oauth = kind == "claude_oauth"
        if self._oauth:
            self._client = anthropic.Anthropic(
                auth_token=value, max_retries=MAX_RETRIES,
                default_headers={"anthropic-beta": CLAUDE_OAUTH_BETA})
        else:
            self._client = anthropic.Anthropic(api_key=value, max_retries=MAX_RETRIES)

    def _system(self, system: str) -> str | list[dict]:
        """The system prompt, with the identity block an OAuth token requires in front of it."""
        if not self._oauth:
            return system
        return [{"type": "text", "text": CLAUDE_CODE_IDENTITY},
                {"type": "text", "text": system}]

    def complete_json(self, *, model: str, system: str, messages: list[dict],
                      schema: dict, max_tokens: int,
                      thinking_tokens: int | None = None) -> dict:
        """One call, constrained to the schema, returned as a dict."""
        with self._client.messages.stream(
                model=model,
                system=self._system(system),
                messages=messages,
                max_tokens=max_tokens,
                **_thinking(thinking_tokens),
                output_config={"format": {"type": "json_schema", "schema": schema}}) as stream:
            message = stream.get_final_message()

        text = _text_of(message)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            # Never log `text`: it derives from the user's own documents or from a job posting.
            logger.error("a response of %d chars did not parse as JSON", len(text))
            raise LlmError("the model's response was not usable JSON") from exc

    def complete_text(self, *, model: str, system: str, messages: list[dict],
                      max_tokens: int, thinking_tokens: int | None = None) -> str:
        """One call, returned as plain text."""
        # Streamed like the JSON path: a whole HTML document at a high ceiling would meet a timeout.
        with self._client.messages.stream(
                model=model,
                system=self._system(system),
                messages=messages,
                max_tokens=max_tokens,
                **_thinking(thinking_tokens)) as stream:
            message = stream.get_final_message()
        warn_if_truncated(getattr(message, "stop_reason", None) == "max_tokens", model, max_tokens)
        return _text_of(message)


    def complete_tools(self, *, model: str, system: str, messages: list[dict], tools: list[dict],
                       max_tokens: int, on_text=None) -> dict:
        """One turn of a tool-using conversation."""
        blocks: list[dict] = []
        with self._client.messages.stream(
                model=model,
                system=self._system(system),
                messages=messages,
                tools=tools,
                max_tokens=max_tokens) as stream:
            if on_text is not None:
                for chunk in stream.text_stream:
                    on_text(chunk)
            message = stream.get_final_message()

        text_parts, tool_uses = [], []
        for block in message.content:
            kind = getattr(block, "type", "")
            if kind == "text":
                text_parts.append(block.text)
                blocks.append({"type": "text", "text": block.text})
            elif kind == "tool_use":
                tool_uses.append({"id": block.id, "name": block.name, "input": block.input})
                blocks.append({"type": "tool_use", "id": block.id, "name": block.name,
                               "input": block.input})

        # The same two checks the other providers' adapters make in `toolcalls._answer`.
        warn_if_truncated(message.stop_reason == "max_tokens", model, max_tokens)
        if not text_parts and not tool_uses:
            raise LlmError(f"Claude wrote nothing ({message.stop_reason or 'no reason given'})")
        return {"text": "".join(text_parts), "tool_uses": tool_uses, "content": blocks,
                "stop_reason": message.stop_reason}


def _thinking(budget: int | None) -> dict:
    """The thinking block for a call, or nothing at all."""
    return {"thinking": {"type": "enabled", "budget_tokens": budget}} if budget else {}


def warn_if_truncated(cut: bool, model: str, max_tokens: int) -> None:
    """Say so when the model ran out of room rather than finishing. Every provider's adapter asks."""
    if cut:
        logger.error("%s hit the %d-token ceiling mid-response — the output is truncated, "
                     "not refused", model, max_tokens)


def _text_of(message) -> str:
    """Concatenate the text blocks of a response, dropping thinking and tool blocks."""
    return "".join(block.text for block in message.content
                   if getattr(block, "type", "") == "text")
