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
                      schema: dict, max_tokens: int) -> dict: ...


class AnthropicClient:
    """`LlmClient` over the real SDK, built from **the user's own credential**.

    Their profile, their spend, no platform cost and no abuse surface. `kind` decides how the
    credential is presented, which is why it is passed rather than sniffed from the token's shape —
    a prefix check is a guess, and the store already knows the answer.
    """

    def __init__(self, value: str, kind: str = "claude_api"):
        import anthropic  # noqa: PLC0415 - keeps the SDK off the import path of everything else

        if kind == "claude_oauth":
            self._client = anthropic.Anthropic(
                auth_token=value, default_headers={"anthropic-beta": CLAUDE_OAUTH_BETA})
        else:
            self._client = anthropic.Anthropic(api_key=value)

    def complete_json(self, *, model: str, system: str, messages: list[dict],
                      schema: dict, max_tokens: int) -> dict:
        """One call, constrained to the schema, returned as a dict.

        Structured output rather than "reply with JSON and please do not add prose": the failure
        being prevented is a profile that parses on the happy path and comes back wrapped in a code
        fence the week the model changes its formatting habits.

        Streamed because the response is a whole profile at a high token ceiling, and a non-streamed
        request that size is the standard way to meet a request timeout instead of an answer.
        """
        with self._client.messages.stream(
                model=model,
                system=system,
                messages=messages,
                max_tokens=max_tokens,
                thinking={"type": "adaptive"},
                output_config={"format": {"type": "json_schema", "schema": schema}}) as stream:
            message = stream.get_final_message()

        text = "".join(block.text for block in message.content
                       if getattr(block, "type", "") == "text")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            # Never log `text`: it derives from the user's own documents or from a job posting.
            logger.error("a response of %d chars did not parse as JSON", len(text))
            raise LlmError("the model's response was not usable JSON") from exc


def client_for(value: str, kind: str) -> LlmClient:
    """Build a client for whichever Claude credential the user has. Either satisfies D2."""
    return AnthropicClient(value, kind)
