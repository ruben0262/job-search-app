"""The Anthropic adapter: build a client from a user credential, make one call.

Plain Messages, not a Managed Agents session. What that buys, in order of importance:

1. **OAuth works.** Managed Agents authenticates by API key only, so the sandboxed design silently
   required every user to buy API credit — which contradicts decision D2, where a subscription token
   is meant to be equally sufficient. It would have surfaced as "my Claude login does not work here"
   at the last step of onboarding.
2. **No beta on the critical path.** Nothing to pin, nothing to break under a running signup.
3. **Nothing leaves the platform but text.** No file uploads, no Files API objects to orphan, so
   account deletion is provably complete rather than needing a second cleanup path
   (`ROADMAP.md` Phase 3 step 11).

The narrow protocol stays for the reason it always did: the tests drive it with a fake rather than
the network, and a client change touches one adapter.
"""
from __future__ import annotations

import json
import logging
from typing import Protocol

logger = logging.getLogger(__name__)

# How many times the SDK retries a 429 or a 5xx before giving up.
#
# **Two, and this is transport retry — not a second attempt at the work.** The distinction matters
# because they look identical in a log and cost differently. A 429 is refused before generation and
# is billed nothing, so retrying it is free; a 529 arriving mid-stream has already produced tokens,
# and retrying re-runs the whole reasoning from scratch on the user's money.
#
# The reference allows exactly one retry and only for transient failures, on the stated grounds that
# "retrying just burns another call" — and it refuses to retry a quota refusal at all, because the
# second call fails the same way and doubles the spend.
#
# This was briefly 8, raised while diagnosing what looked like relentless rate limiting and turned
# out to be the missing OAuth identity block (every request refused with a `rate_limit_error` that
# no amount of retrying could clear). With the cause fixed, 8 is a spend multiplier and a long wait
# for a user watching a CV build.
#
# The *logic*-level retry is separate and stays at one: `MAX_TAILOR_CALLS` gives a CV a single
# second attempt, with feedback, when the page count is wrong.
MAX_RETRIES = 2

# The identity block a subscription OAuth token is entitled to. Verbatim — it is compared, not
# parsed, and a reworded version is refused exactly as a missing one is.
CLAUDE_CODE_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."

# An OAuth token authenticates on `Authorization: Bearer` and needs this beta header; an API key
# goes on `x-api-key` and does not. Sending one on the other's channel is the single most common way
# a working credential looks broken (`libs.credentials.validate` carries the same constant).
CLAUDE_OAUTH_BETA = "oauth-2025-04-20"


# Either satisfies the Claude requirement (D2). Defined here because both callers need the same
# answer to "which credential kinds count", and two lists is how they stop agreeing.
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
    """`LlmClient` over the real SDK, built from **the user's own credential**.

    Their profile, their spend, no platform cost and no abuse surface. `kind` decides how the
    credential is presented, which is why it is passed rather than sniffed from the token's shape —
    a prefix check is a guess, and the store already knows the answer.
    """

    def __init__(self, value: str, kind: str = "claude_api"):
        import anthropic  # noqa: PLC0415 - keeps the SDK off the import path of everything else

        # **Eight retries, not the SDK's two.** Every call here runs on a *user's* credential, and
        # a personal plan rate-limits far harder than a platform key: a batch of a dozen CV builds
        # exhausts the default budget partway through and surfaces as a dead queue job on work the
        # user is waiting for. The SDK backs off exponentially and honours `retry-after`, so the
        # cost of a higher ceiling is patience on a call that was going to fail anyway.
        self._oauth = kind == "claude_oauth"
        if self._oauth:
            self._client = anthropic.Anthropic(
                auth_token=value, max_retries=MAX_RETRIES,
                default_headers={"anthropic-beta": CLAUDE_OAUTH_BETA})
        else:
            self._client = anthropic.Anthropic(api_key=value, max_retries=MAX_RETRIES)

    def _system(self, system: str) -> str | list[dict]:
        """The system prompt, with the identity block an OAuth token requires in front of it.

        **A subscription OAuth token is entitled to the Claude Code product, not to the raw API**,
        and the entitlement is checked against the first system block. Send anything else as the
        first block and the request is refused — with a `rate_limit_error`, which is the part that
        cost a day: it reads as "you have used your quota", it is retried politely by every client
        in the world, and it never clears because nothing was ever consumed.

        Measured on 2026-07-29 against a live subscription token: an identical call is `429` with a
        plain system prompt and `200` with this block in front. Haiku happened to pass either way,
        which is why labelling and CV builds worked while the Opus setup agent failed on every
        OAuth account.

        API keys need none of this and get the string unchanged — sending the block on that channel
        would claim to be a product the caller is not.
        """
        if not self._oauth:
            return system
        return [{"type": "text", "text": CLAUDE_CODE_IDENTITY},
                {"type": "text", "text": system}]

    def complete_json(self, *, model: str, system: str, messages: list[dict],
                      schema: dict, max_tokens: int,
                      thinking_tokens: int | None = None) -> dict:
        """One call, constrained to the schema, returned as a dict.

        Structured output rather than "reply with JSON and please do not add prose": the failure
        being prevented is a profile that parses on the happy path and comes back wrapped in a code
        fence the week the model changes its formatting habits.

        Streamed because the response is a whole profile at a high token ceiling, and a non-streamed
        request that size is the standard way to meet a request timeout instead of an answer.
        """
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
        """One call, returned as plain text. What docgen needs, and it cannot use the JSON path.

        A tailored CV **is** an HTML document, and asking for it inside a JSON string field would
        make the model escape a few thousand characters of markup by hand — a new failure mode for
        no benefit, since there is nothing to validate the shape of but "did an `<html>` come back",
        which the caller checks anyway.

        `thinking_tokens` is a budget, not a switch. The reference instance measured disabling
        thinking at ~8x faster and ~3x cheaper, and also measured what it cost: duplicate bullet
        openers went from 1 to 5 and the style discipline degraded. Default thinking stays on for
        documents an employer reads; `0` is the escape hatch, and taking it needs an A/B.
        """
        # Streamed for the same reason the JSON path is: a whole HTML document at a high token
        # ceiling is exactly the request that meets a timeout instead of an answer.
        with self._client.messages.stream(
                model=model,
                system=self._system(system),
                messages=messages,
                max_tokens=max_tokens,
                **_thinking(thinking_tokens)) as stream:
            message = stream.get_final_message()
        _warn_if_truncated(message, model, max_tokens)
        return _text_of(message)


    def complete_tools(self, *, model: str, system: str, messages: list[dict], tools: list[dict],
                       max_tokens: int, on_text=None) -> dict:
        """One turn of a tool-using conversation. Returns the reply and any tool calls it made.

        **One turn, not the whole loop.** The caller executes the tools and calls again — which is
        what keeps every write to somebody's profile visible to the code that audits it, rather
        than happening inside a helper (`libs/agents`).

        `on_text` receives text as it arrives, so a chat can render a reply while a frontier model
        is still working. A setup turn can run for minutes; without it the page shows nothing until
        the turn ends.

        No `thinking` parameter: this is Opus 5, where thinking is on by default and a stated
        `budget_tokens` is rejected outright.
        """
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

        return {"text": "".join(text_parts), "tool_uses": tool_uses, "content": blocks,
                "stop_reason": message.stop_reason}


def _thinking(budget: int | None) -> dict:
    """The thinking block for a call, or nothing at all.

    **Neither shape is universal, so the default is to say nothing.** `adaptive` was hardcoded here
    and became a 400 — "adaptive thinking is not supported on this model" — the moment a call went
    to Haiku; `budget_tokens` is rejected by Opus 5 the same way. The only setting that is correct
    everywhere is the model's own, so a caller that states a budget gets it and a caller that does
    not gets whatever the model does by default.

    Returned as kwargs rather than a value, so "no opinion" can mean an absent parameter instead of
    a sentinel every call site would have to know about.
    """
    return {"thinking": {"type": "enabled", "budget_tokens": budget}} if budget else {}


def _warn_if_truncated(message, model: str, max_tokens: int) -> None:
    """Say so when the model ran out of room rather than finishing.

    **A truncated document and a refused one look identical to the caller** — both arrive as a
    reply with no closing `</html>` in it — and the two need opposite fixes. Docgen logged "the
    tailor call returned 2367 chars with no HTML document in it" for both, which is enough to know
    something failed and not enough to know what.

    Worth noting for anyone raising the ceiling: the thinking budget is spent *inside* `max_tokens`,
    so a 16,000-token call with 4,096 of thinking has under 12,000 left for the document.
    """
    if getattr(message, "stop_reason", None) == "max_tokens":
        logger.error("%s hit the %d-token ceiling mid-response — the output is truncated, "
                     "not refused", model, max_tokens)


def _text_of(message) -> str:
    """Concatenate the text blocks of a response, dropping thinking and tool blocks.

    Shared by both call shapes: a thinking block leaking into a returned document would put the
    model's reasoning inside somebody's CV.
    """
    return "".join(block.text for block in message.content
                   if getattr(block, "type", "") == "text")


def client_for(value: str, kind: str) -> LlmClient:
    """Build a client for whichever Claude credential the user has. Either satisfies D2."""
    return AnthropicClient(value, kind)
