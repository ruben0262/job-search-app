"""One turn of a setup conversation: call, execute tools, call again, until the agent stops."""
from __future__ import annotations

import logging
import uuid as _uuid
from collections.abc import Callable, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from libs.agents import (
    COMMAND_TITLES,
    MAX_TOKENS,
    MAX_TOOL_ITERATIONS,
    SETUP_MODEL,
    TOOLS,
    system_prompt,
)
from libs.agents.tools import SetupTools
from libs.db.models import DEFAULT_TITLE, SetupConversation, SetupMessage
from libs.db.models.setup import SETUP_AGENTS
from libs.profiles import harddata

logger = logging.getLogger(__name__)

# What a turn is allowed to have written before the loop stops.
ITERATION_LIMIT_MESSAGE = ("I have run out of steps for this turn. Everything written so far is "
                           "saved — say 'continue' and I will pick up from there.")


def history(session: Session, user_id: _uuid.UUID,
            conversation_id: _uuid.UUID) -> list[dict]:
    """This conversation so far, in the shape the API takes it back."""
    rows = session.scalars(
        select(SetupMessage)
        .where(SetupMessage.user_id == user_id,
               SetupMessage.conversation_id == conversation_id)
        .order_by(SetupMessage.id)).all()

    messages: list[dict] = []
    for row in rows:
        if row.role == "user":
            messages.append({"role": "user", "content": row.content})
            continue

        blocks: list[dict] = []
        if row.content:
            blocks.append({"type": "text", "text": row.content})
        for call in row.tool_calls or []:
            blocks.append({"type": "tool_use", "id": call["id"], "name": call["name"],
                           "input": call["input"]})
        if blocks:
            messages.append({"role": "assistant", "content": blocks})
        results = [{"type": "tool_result", "tool_use_id": call["id"], "content": call["result"]}
                   for call in row.tool_calls or []]
        if results:
            messages.append({"role": "user", "content": results})
    return messages


def run_turn(session: Session, user_id: _uuid.UUID, *, client, message: str,
             conversation_id: _uuid.UUID,
             agent: str = "setup",
             reply_row: SetupMessage | None = None,
             on_text: Callable[[str], None] | None = None) -> dict:
    """Take one message from the user and run the agent until it stops calling tools."""
    if agent not in SETUP_AGENTS:
        raise ValueError(f"unknown setup agent {agent!r}; known: {SETUP_AGENTS}")

    if reply_row is None:
        # Synchronous path: write the user's message and a pending reply here.
        session.add(SetupMessage(user_id=user_id, conversation_id=conversation_id,
                                 role="user", content=message, agent=agent))
        touch_conversation(session, user_id, conversation_id, message)
        reply_row = SetupMessage(user_id=user_id, conversation_id=conversation_id,
                                 role="assistant", content="", agent=agent, status="pending")
        session.add(reply_row)
    session.commit()

    tools = SetupTools(session, user_id)
    messages = history(session, user_id, conversation_id)
    # The command's instructions, then the typed half of this person's profile. Injected rather than
    # offered as a tool: nothing in it needs judgement, and a tool the model may call is one it may
    # skip — which is how one account's two new links never reached the prompt writing its contact
    # line (`libs.profiles.harddata`).
    prompt = system_prompt(agent) + harddata.render(session, user_id)

    reply_parts: list[str] = []
    executed: list[dict] = []

    # Streamed into the row as it arrives.
    streaming: list[str] = []

    def _stream(chunk: str) -> None:
        streaming.append(chunk)
        reply_row.content = "\n\n".join([*reply_parts, "".join(streaming)]).strip()
        reply_row.status = "streaming"
        session.commit()
        if on_text is not None:
            on_text(chunk)

    for step in range(1, MAX_TOOL_ITERATIONS + 1):
        # The stop is checked here, between tool steps, and nowhere else.
        session.refresh(reply_row)
        if reply_row.status == "cancelling":
            reply_row.content = "\n\n".join([*reply_parts, "".join(streaming)]).strip()
            reply_row.tool_calls = executed or None
            reply_row.status = "cancelled"
            session.commit()
            logger.info("setup: %s stopped by the user after %d step(s)", user_id, step - 1)
            # Same shape as the normal return, plus the flag: a stopped turn keeps its work.
            return {"reply": reply_row.content,
                    "tools_run": [call["name"] for call in executed],
                    "files_written": _written(executed),
                    "scoring_edits": tools.scoring_edits,
                    "cancelled": True}

        streaming.clear()
        answer = client.complete_tools(model=SETUP_MODEL, system=prompt, messages=messages,
                                       tools=TOOLS, max_tokens=MAX_TOKENS, on_text=_stream)
        if answer["text"]:
            reply_parts.append(answer["text"])
        streaming.clear()

        if not answer["tool_uses"]:
            break

        messages.append({"role": "assistant", "content": answer["content"]})
        results = []
        for call in answer["tool_uses"]:
            output = tools.run(call["name"], call["input"] or {})
            results.append({"type": "tool_result", "tool_use_id": call["id"], "content": output})
            executed.append({**call, "result": output})
        messages.append({"role": "user", "content": results})
        logger.debug("setup: %s completed tool step %d", user_id, step)
    else:
        logger.warning("setup: %s hit the %d-step tool limit", user_id, MAX_TOOL_ITERATIONS)
        reply_parts.append(ITERATION_LIMIT_MESSAGE)

    reply = "\n\n".join(part for part in reply_parts if part.strip())
    reply_row.content = reply
    reply_row.tool_calls = executed or None
    reply_row.status = "done"
    session.commit()

    return {"reply": reply, "tools_run": [call["name"] for call in executed],
            "files_written": _written(executed), "scoring_edits": tools.scoring_edits}


def touch_conversation(session: Session, user_id: _uuid.UUID, conversation_id: _uuid.UUID,
           message: str) -> None:
    """Move the conversation to the top of the list, and name it if it has no name yet."""
    row = session.get(SetupConversation, conversation_id)
    if row is None or row.user_id != user_id:
        return
    row.last_message_at = func.now()
    if row.title == DEFAULT_TITLE:
        first = " ".join(message.split())
        named = COMMAND_TITLES.get(first.lstrip("/").split()[0]) if first.startswith("/") else None
        if named:
            row.title = named
        else:
            row.title = (first[:57] + "…") if len(first) > 58 else first or DEFAULT_TITLE


def _written(executed: Sequence[dict]) -> list[str]:
    """Which files this turn actually saved — what the file panel needs to refresh."""
    return [call["input"]["name"] for call in executed
            if call["name"] == "write_profile_file" and "name" in (call["input"] or {})]
