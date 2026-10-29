"""Tool calls in Gemini's and OpenAI's own words, translated at the edge of `libs.llm`.

The help chat speaks one shape, Anthropic's: `tool_use` blocks in an assistant message and
`tool_result` blocks in the user message after it, which `AnthropicClient` sends as they are. Gemini
calls the same things `functionCall` and `functionResponse` parts, OpenAI `tool_calls` and `tool`
messages. These functions turn the one shape into each provider's request, and each streamed answer
back into what `complete_tools` returns everywhere: `{"text", "tool_uses", "content", "stop_reason"}`,
with the text handed to `on_text` as it arrives.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterable

from libs.llm.client import LlmError, warn_if_truncated


def gemini_body(system: str, messages: list[dict], tools: list[dict], max_tokens: int) -> dict:
    """One `streamGenerateContent` request."""
    # A function's answer is matched to its call by name, which a `tool_result` does not carry.
    names = {block["id"]: block["name"] for message in messages
             for block in _blocks(message["content"]) if block["type"] == "tool_use"}
    contents = []
    for message in messages:
        parts = []
        for block in _blocks(message["content"]):
            if block["type"] == "text":
                parts.append({"text": block["text"]})
            elif block["type"] == "tool_use":
                # Gemini 3 refuses a conversation whose function call comes back without its signature.
                parts.append({"functionCall": {"name": block["name"], "args": block["input"]},
                              **({"thoughtSignature": block["signature"]} if block.get("signature")
                                 else {})})
            else:
                parts.append({"functionResponse": {"name": names[block["tool_use_id"]],
                                                   "response": {"result": block["content"]}}})
        contents.append({"role": "model" if message["role"] == "assistant" else "user", "parts": parts})
    # A function without parameters declares none: Gemini refuses an object with no properties.
    declared = [{"name": tool["name"], "description": tool["description"],
                 **({"parameters": tool["input_schema"]} if tool["input_schema"].get("properties")
                    else {})} for tool in tools]
    return {"systemInstruction": {"parts": [{"text": system}]}, "contents": contents,
            **({"tools": [{"functionDeclarations": declared}]} if declared else {}),
            "generationConfig": {"maxOutputTokens": max_tokens}}


def gemini_answer(events: Iterable[dict], on_text: Callable[[str], None] | None, model: str,
                  max_tokens: int) -> dict:
    """The streamed answer: its text as it arrives, then its calls."""
    text: list[str] = []
    uses: list[dict] = []
    finish = blocked = None
    for event in events:
        candidate = (event.get("candidates") or [{}])[0]
        for part in (candidate.get("content") or {}).get("parts") or []:
            if "functionCall" in part:
                call = part["functionCall"]
                uses.append({"type": "tool_use", "id": call.get("id") or f"call_{len(uses)}",
                             "name": call["name"], "input": call.get("args") or {},
                             "signature": part.get("thoughtSignature")})
            elif part.get("text") and not part.get("thought"):
                _say(text, part["text"], on_text)
        finish = candidate.get("finishReason") or finish
        blocked = (event.get("promptFeedback") or {}).get("blockReason") or blocked
    return _answer(text, uses, cut=finish == "MAX_TOKENS", model=model, max_tokens=max_tokens,
                   nothing=f"Gemini wrote nothing ({finish or blocked or 'no reason given'})")


def openai_body(model: str, system: str, messages: list[dict], tools: list[dict],
                max_tokens: int) -> dict:
    """One streamed Chat Completions request."""
    out: list[dict] = [{"role": "developer", "content": system}]
    for message in messages:
        blocks = _blocks(message["content"])
        said = "".join(block["text"] for block in blocks if block["type"] == "text")
        if message["role"] == "assistant":
            calls = [{"id": block["id"], "type": "function",
                      "function": {"name": block["name"], "arguments": json.dumps(block["input"])}}
                     for block in blocks if block["type"] == "tool_use"]
            out.append({"role": "assistant", "content": said or None,
                        **({"tool_calls": calls} if calls else {})})
            continue
        out += [{"role": "tool", "tool_call_id": block["tool_use_id"], "content": block["content"]}
                for block in blocks if block["type"] == "tool_result"]
        if said:
            out.append({"role": "user", "content": said})
    return {"model": model, "messages": out, "max_completion_tokens": max_tokens, "stream": True,
            **({"tools": [{"type": "function", "function": {
                "name": tool["name"], "description": tool["description"],
                "parameters": tool["input_schema"]}} for tool in tools]} if tools else {})}


def openai_answer(events: Iterable[dict], on_text: Callable[[str], None] | None, model: str,
                  max_tokens: int) -> dict:
    """The streamed answer. A call arrives in pieces: its id and name once, its arguments in parts."""
    text: list[str] = []
    calls: dict[int, dict] = {}
    finish = None
    for event in events:
        choice = (event.get("choices") or [{}])[0]
        delta = choice.get("delta") or {}
        if delta.get("content"):
            _say(text, delta["content"], on_text)
        for piece in delta.get("tool_calls") or []:
            call = calls.setdefault(piece.get("index", 0), {"id": "", "name": "", "arguments": ""})
            function = piece.get("function") or {}
            call["id"] = piece.get("id") or call["id"]
            call["name"] += function.get("name") or ""
            call["arguments"] += function.get("arguments") or ""
        finish = choice.get("finish_reason") or finish
    try:
        uses = [{"type": "tool_use", "id": call["id"], "name": call["name"],
                 "input": json.loads(call["arguments"] or "{}")} for _, call in sorted(calls.items())]
    except json.JSONDecodeError as exc:
        raise LlmError("OpenAI called a tool with arguments that are not JSON") from exc
    return _answer(text, uses, cut=finish == "length", model=model, max_tokens=max_tokens,
                   nothing=f"OpenAI wrote nothing ({finish or 'no reason given'})")


def _blocks(content: str | list[dict]) -> list[dict]:
    return [{"type": "text", "text": content}] if isinstance(content, str) else content


def _say(text: list[str], chunk: str, on_text: Callable[[str], None] | None) -> None:
    text.append(chunk)
    if on_text is not None:
        on_text(chunk)


def _answer(text: list[str], uses: list[dict], *, cut: bool, model: str, max_tokens: int,
            nothing: str) -> dict:
    """The one shape every provider's `complete_tools` returns. An empty answer is a refusal."""
    warn_if_truncated(cut, model, max_tokens)
    if not text and not uses:
        raise LlmError(nothing)
    said = "".join(text)
    return {"text": said,
            "tool_uses": [{key: use[key] for key in ("id", "name", "input")} for use in uses],
            "content": [{"type": "text", "text": said}] * bool(said) + uses,
            "stop_reason": "tool_use" if uses else "end_turn"}
