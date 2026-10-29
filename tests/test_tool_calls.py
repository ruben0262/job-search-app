"""Tool calls on Gemini and OpenAI: the help chat's one shape sent in each provider's words, and each
provider's streamed answer read back into it. Their real adapters, over a mock transport."""
from __future__ import annotations

import json

import pytest

httpx = pytest.importorskip("httpx")

from libs.llm import LlmError
from libs.llm.rest import GeminiClient, OpenAIClient

TOOLS = [{"name": "why_job", "description": "Find a job.",
          "input_schema": {"type": "object", "properties": {"search": {"type": "string"}},
                           "required": ["search"]}},
         {"name": "my_words", "description": "Their words.",
          "input_schema": {"type": "object", "properties": {}, "required": []}}]
# One round in: asked, a lookup called — with Gemini's signature on it — and its answer back.
ROUND = [{"role": "user", "content": "Why is the Acme job here?"},
         {"role": "assistant", "content": [
             {"type": "text", "text": "Let me look."},
             {"type": "tool_use", "id": "call-1", "name": "why_job", "input": {"search": "acme"},
              "signature": "c2ln"}]},
         {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call-1",
                                       "content": "Found by python."}]}]


def _client(adapter, body: bytes, status: int = 200):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, content=body, headers={"content-type": "text/event-stream"})

    return adapter("key-" + "k" * 30, http=httpx.Client(transport=httpx.MockTransport(handler))), seen


def _sse(*events: dict) -> bytes:
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events).encode()


def _turn(client) -> tuple[dict, list[str]]:
    chunks: list[str] = []
    answer = client.complete_tools(model="the-model", system="Help.", messages=ROUND, tools=TOOLS,
                                   max_tokens=300, on_text=chunks.append)
    return answer, chunks


def test_gemini_streams_its_text_reads_its_call_and_is_sent_the_round_before():
    client, seen = _client(GeminiClient, _sse(
        {"candidates": [{"content": {"parts": [{"text": "The Acme job "}]}}]},
        {"candidates": [{"content": {"parts": [
            {"text": "matched python."},
            {"functionCall": {"name": "my_words", "args": {}}, "thoughtSignature": "bmV4dA"}]},
            "finishReason": "STOP"}]}))

    answer, chunks = _turn(client)

    assert chunks == ["The Acme job ", "matched python."]
    assert answer["text"] == "The Acme job matched python."
    assert answer["tool_uses"] == [{"id": "call_0", "name": "my_words", "input": {}}]
    assert answer["content"][-1]["signature"] == "bmV4dA", "kept, to go back with the next round"
    body = json.loads(seen[0].content)
    assert str(seen[0].url).endswith("/models/the-model:streamGenerateContent?alt=sse")
    assert body["systemInstruction"] == {"parts": [{"text": "Help."}]}
    assert body["tools"] == [{"functionDeclarations": [
        {"name": "why_job", "description": "Find a job.", "parameters": TOOLS[0]["input_schema"]},
        {"name": "my_words", "description": "Their words."}]}]
    assert body["contents"] == [
        {"role": "user", "parts": [{"text": "Why is the Acme job here?"}]},
        {"role": "model", "parts": [{"text": "Let me look."},
                                    {"functionCall": {"name": "why_job", "args": {"search": "acme"}},
                                     "thoughtSignature": "c2ln"}]},
        {"role": "user", "parts": [{"functionResponse": {
            "name": "why_job", "response": {"result": "Found by python."}}}]}]


def test_openai_streams_its_text_and_puts_a_call_together_from_its_pieces():
    client, seen = _client(OpenAIClient, _sse(
        {"choices": [{"delta": {"role": "assistant", "content": "Checking "}}]},
        {"choices": [{"delta": {"content": "your words."}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_abc", "type": "function",
                                                "function": {"name": "why_job", "arguments": '{"sea'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'rch": "acme"}'}}]},
                      "finish_reason": "tool_calls"}]}) + b"data: [DONE]\n\n")

    answer, chunks = _turn(client)

    assert chunks == ["Checking ", "your words."]
    assert answer["tool_uses"] == [{"id": "call_abc", "name": "why_job", "input": {"search": "acme"}}]
    body = json.loads(seen[0].content)
    assert body["stream"] is True and body["max_completion_tokens"] == 300
    assert body["tools"][0] == {"type": "function", "function": {
        "name": "why_job", "description": "Find a job.", "parameters": TOOLS[0]["input_schema"]}}
    assert body["messages"] == [
        {"role": "developer", "content": "Help."},
        {"role": "user", "content": "Why is the Acme job here?"},
        {"role": "assistant", "content": "Let me look.", "tool_calls": [{
            "id": "call-1", "type": "function",
            "function": {"name": "why_job", "arguments": '{"search": "acme"}'}}]},
        {"role": "tool", "tool_call_id": "call-1", "content": "Found by python."}]


@pytest.mark.parametrize("adapter", [GeminiClient, OpenAIClient])
@pytest.mark.parametrize(("status", "final"), [(400, True), (401, True), (429, False), (503, False)])
def test_a_refused_turn_is_final_and_a_busy_provider_is_worth_asking_again(adapter, status, final):
    client, _ = _client(adapter, json.dumps({"error": {"message": "their reason"}}).encode(), status)

    with pytest.raises(Exception) as raised:
        _turn(client)

    assert isinstance(raised.value, LlmError) is final
    assert "their reason" in str(raised.value)


def test_an_answer_with_nothing_in_it_is_a_refusal_not_an_empty_reply():
    client, _ = _client(GeminiClient, _sse({"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}}))

    with pytest.raises(LlmError, match="SAFETY"):
        _turn(client)
