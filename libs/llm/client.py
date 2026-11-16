"""The Anthropic adapter, and the shape every provider's adapter shares (`LlmClient`)."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Protocol

from libs.db.models.queue import CLAIM_TIMEOUT_MIN

logger = logging.getLogger(__name__)

# How many times the SDK retries a 429 or a 5xx before giving up.
MAX_RETRIES = 2

# One reply is one whole document, so the read timeout is the time it takes to write it. Derived
# from the docgen lane's claim rather than chosen: a CV and its one revision, the most a build
# makes, finish inside the claim with a fifth of it left for rendering. Every provider's, Claude's
# included: left to its SDK's ten minutes, one Claude call could outlive the claim it ran under.
TIMEOUT_SEC = CLAIM_TIMEOUT_MIN["docgen"] * 60 * 2 / 5


class LlmError(RuntimeError):
    """A call did not produce usable output. Callers decide whether that is fatal."""


@dataclass(frozen=True)
class Usage:
    """What one call used, as its provider counts it: the tokens that went in (the prompt, cached or not)
    and the tokens that came out (the answer, and the thinking before it)."""

    input_tokens: int
    output_tokens: int


class Reply(str):
    """A model's answer. It is its text, and carries what the call used where the provider says
    (`usage`): the docgen worker logs it with every document, and the CV bench counts it per wording.
    And which model wrote it, as the provider names it (`model`), which need not be the one asked for: a
    plan may not include that one, and a name may be answered by a dated version of it. A document is kept
    by this (owner, 2026-11-15)."""

    __slots__ = ("model", "usage")

    def __new__(cls, text: str, usage: Usage | None = None, model: str | None = None) -> Reply:
        reply = super().__new__(cls, text)
        reply.usage = usage
        reply.model = model if isinstance(model, str) and model else None
        return reply


def counted(tokens_in: object, tokens_out: object) -> Usage | None:
    """A call's use from what a provider reported, or None where it did not count."""
    if isinstance(tokens_in, int) and isinstance(tokens_out, int):
        return Usage(tokens_in, tokens_out)
    return None


class LlmClient(Protocol):
    def complete_text(self, *, model: str, system: str, messages: list[dict],
                      max_tokens: int, effort: str | None = None) -> str: ...

    def complete_tools(self, *, model: str, system: str, messages: list[dict], tools: list[dict],
                       max_tokens: int, on_text=None) -> dict: ...


class AnthropicClient:
    """`LlmClient` over the real SDK, built from the person's own API key. A Claude subscription signs in
    through Claude Code instead (`libs.llm.cli`)."""

    def __init__(self, value: str):
        import anthropic  # noqa: PLC0415 - keeps the SDK off the import path of everything else

        self._client = anthropic.Anthropic(api_key=value, max_retries=MAX_RETRIES, timeout=TIMEOUT_SEC)

    def complete_text(self, *, model: str, system: str, messages: list[dict],
                      max_tokens: int, effort: str | None = None) -> str:
        """One call, returned as plain text."""
        # Streamed: a whole document at a high ceiling would meet a timeout.
        with self._client.messages.stream(
                model=model,
                system=system,
                messages=messages,
                max_tokens=max_tokens,
                **_thinking(model, effort)) as stream:
            message = stream.get_final_message()
        warn_if_truncated(getattr(message, "stop_reason", None) == "max_tokens", model, max_tokens)
        used = getattr(message, "usage", None)
        cached = sum(getattr(used, name, None) or 0
                     for name in ("cache_creation_input_tokens", "cache_read_input_tokens"))
        tokens_in = getattr(used, "input_tokens", None)
        return Reply(_text_of(message), counted(tokens_in + cached if isinstance(tokens_in, int) else None,
                                                getattr(used, "output_tokens", None)),
                     getattr(message, "model", None))


    def complete_tools(self, *, model: str, system: str, messages: list[dict], tools: list[dict],
                       max_tokens: int, on_text=None) -> dict:
        """One turn of a tool-using conversation."""
        blocks: list[dict] = []
        with self._client.messages.stream(
                model=model,
                system=system,
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


# A Claude model's version in its name: "claude-haiku-5-5", "claude-haiku-4-5-20251001",
# "claude-sonnet-4-20250514".
_VERSION = re.compile(r"^claude-(?:opus|sonnet|haiku)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?$")


# Claude 4.5 and before take a budget, not an effort: 4,096 measured better on writing quality than
# their thinking off (2026-07-20).
OLD_MODEL_THINKING_TOKENS = 4_096


def _thinking(model: str, effort: str | None) -> dict:
    """How hard a call thinks, in the form the model takes, or nothing at all. From 4.6 Claude thinks
    adaptively, as deep as `effort` asks, and Haiku 5.5 answers a token budget with a 400 (Anthropic's
    Haiku 5.5 migration guide, 2026-11-10); 4.5 and before think within a budget. A name that says no
    version is taken as a newer model."""
    if not effort:
        return {}
    version = _VERSION.match(model)
    if version and (int(version.group(1)), int(version.group(2) or 0)) <= (4, 5):
        return {"thinking": {"type": "enabled", "budget_tokens": OLD_MODEL_THINKING_TOKENS}}
    return {"thinking": {"type": "adaptive"}, "output_config": {"effort": effort}}


def warn_if_truncated(cut: bool, model: str, max_tokens: int) -> None:
    """Say so when the model ran out of room rather than finishing. Every provider's adapter asks."""
    if cut:
        logger.error("%s hit the %d-token ceiling mid-response — the output is truncated, "
                     "not refused", model, max_tokens)


def _text_of(message) -> str:
    """Concatenate the text blocks of a response, dropping thinking and tool blocks."""
    return "".join(block.text for block in message.content
                   if getattr(block, "type", "") == "text")
