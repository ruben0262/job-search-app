"""The Gemini and OpenAI adapters: one plain-text call each, over their REST APIs.

Thin on purpose. A document is one request and one reply, so httpx and a page of code do what an SDK
would, without a second SDK per provider in the docgen image. Text only: tool calls are a later
phase (ROADMAP Phase 8, step 7).
"""
from __future__ import annotations

import logging

import httpx

from libs.db.models.queue import CLAIM_TIMEOUT_MIN
from libs.llm.client import LlmError, warn_if_truncated
from libs.secrets import redact

logger = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

# One reply is one whole document, so the read timeout is the time it takes to write it. Derived
# from the docgen lane's claim rather than chosen: a CV and its one revision, the most a build
# makes, finish inside the claim with a fifth of it left for rendering.
TIMEOUT_SEC = CLAIM_TIMEOUT_MIN["docgen"] * 60 * 2 / 5


class GeminiClient:
    """`complete_text` on a Gemini API key. The key travels in a header, never in the URL.

    `http` is injectable so tests need no network; by default each call is its own request.
    """

    def __init__(self, key: str, http: httpx.Client | None = None):
        self._headers, self._http = {"x-goog-api-key": key}, http

    def complete_text(self, *, model: str, system: str, messages: list[dict],
                      max_tokens: int, thinking_tokens: int | None = None) -> str:
        """One call. `thinking_tokens` is Anthropic's budget; Gemini thinks by its own default."""
        reply = _post(self._http, "Gemini", GEMINI_URL.format(model=model), self._headers, {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "model" if message["role"] == "assistant" else "user",
                          "parts": [{"text": message["content"]}]} for message in messages],
            "generationConfig": {"maxOutputTokens": max_tokens}})
        candidate = (reply.get("candidates") or [{}])[0]
        warn_if_truncated(candidate.get("finishReason") == "MAX_TOKENS", model, max_tokens)
        text = "".join(part.get("text", "") for part in (candidate.get("content") or {})
                       .get("parts") or [] if not part.get("thought"))
        if not text:
            # A refusal, not a fault: asking again gets the same answer.
            reason = (candidate.get("finishReason")
                      or (reply.get("promptFeedback") or {}).get("blockReason"))
            raise LlmError(f"Gemini wrote nothing ({reason or 'no reason given'})")
        return text


class OpenAIClient:
    """`complete_text` on an OpenAI API key, through Chat Completions. `http` as for Gemini."""

    def __init__(self, key: str, http: httpx.Client | None = None):
        self._headers, self._http = {"Authorization": f"Bearer {key}"}, http

    def complete_text(self, *, model: str, system: str, messages: list[dict],
                      max_tokens: int, thinking_tokens: int | None = None) -> str:
        """One call. `thinking_tokens` is Anthropic's budget; OpenAI reasons at its own default."""
        reply = _post(self._http, "OpenAI", OPENAI_URL, self._headers, {
            "model": model,
            "messages": [{"role": "developer", "content": system},
                         *({"role": message["role"], "content": message["content"]}
                           for message in messages)],
            "max_completion_tokens": max_tokens})
        choice = (reply.get("choices") or [{}])[0]
        warn_if_truncated(choice.get("finish_reason") == "length", model, max_tokens)
        text = (choice.get("message") or {}).get("content") or ""
        if not text:
            raise LlmError(f"OpenAI wrote nothing ({choice.get('finish_reason') or 'no reason'})")
        return text


def _post(http: httpx.Client | None, provider: str, url: str, headers: dict, body: dict) -> dict:
    """One request. A refusal is `LlmError` (final); busy or broken is `RuntimeError` (retried)."""
    response = (http or httpx).post(url, headers=headers, json=body, timeout=TIMEOUT_SEC)
    if response.status_code == 200:
        return response.json()
    # Redacted while the key is still registered: this text outlives the call, in `last_error`.
    detail = redact(_message(response))[:300]
    if response.status_code == 429 or response.status_code >= 500:
        raise RuntimeError(f"{provider} answered {response.status_code}: {detail}")
    raise LlmError(f"{provider} refused the request ({response.status_code}): {detail}")


def _message(response: httpx.Response) -> str:
    """The provider's own explanation: both put it at `error.message`."""
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        return response.text
    return str(error.get("message") if isinstance(error, dict) else error or response.text)
