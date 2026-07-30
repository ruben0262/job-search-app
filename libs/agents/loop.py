"""One turn of a setup conversation: call, execute tools, call again, until the agent stops.

The same shape the SDK's tool runner implements, kept explicit for two reasons — every tool call
here writes to somebody's profile and belongs in an audit line, and the runner is a beta surface
this platform does not otherwise depend on.

**The transcript is the state.** There is no session object holding a conversation in memory: each
turn is rebuilt from `setup_messages`, so a worker restart, a closed browser tab, or a user coming
back a week later all resume the same way.

**One conversation at a time.** Every read here filters on `conversation_id`, so a chat can never
see another chat's transcript — which is the whole reason for having more than one. What crosses
between them is the profile the tools write, not the talking.
"""
from __future__ import annotations

import logging
import uuid as _uuid
from collections.abc import Callable, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from libs.agents import MAX_TOKENS, MAX_TOOL_ITERATIONS, SETUP_MODEL, TOOLS, system_prompt
from libs.agents.tools import SetupTools
from libs.db.models import DEFAULT_TITLE, SetupConversation, SetupMessage
from libs.db.models.setup import SETUP_AGENTS

logger = logging.getLogger(__name__)

# What a turn is allowed to have written before the loop stops. Reached only by a malfunctioning
# turn — `/setup` Path A reads a document set and writes seven files, comfortably inside it.
ITERATION_LIMIT_MESSAGE = ("I have run out of steps for this turn. Everything written so far is "
                           "saved — say 'continue' and I will pick up from there.")


def history(session: Session, user_id: _uuid.UUID,
            conversation_id: _uuid.UUID) -> list[dict]:
    """This conversation so far, in the shape the API takes it back.

    Tool calls and their results are replayed as their own turns, so the model sees the file it
    wrote rather than a summary of having written one — the difference between it knowing what is
    in `context.md` and it guessing.

    Scoped to one conversation **and** one user: the conversation id alone would be enough, and
    carrying the user as well is what makes a guessed id useless.
    """
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
    """Take one message from the user and run the agent until it stops calling tools.

    Returns what the caller needs to render and to decide about retraining: the reply, the tools
    that ran, and which scoring-relevant files were touched.

    **Commits as it goes**, unlike the queue workers. A setup conversation is minutes long and the
    user is watching; losing ten minutes of interview because the last call failed would be the
    worst possible moment to be strict about transactions.

    `reply_row` is the pending assistant message this turn fills in. The queued path creates it
    before enqueuing — so the page can show the turn as accepted the moment it is — and passes it
    here; a caller that passes nothing gets the whole exchange written for it.
    """
    if agent not in SETUP_AGENTS:
        raise ValueError(f"unknown setup agent {agent!r}; known: {SETUP_AGENTS}")

    if reply_row is None:
        # No row was prepared, so this is the synchronous path: write the user's message and a
        # pending reply here. The queued path does both before enqueuing, so the page can render
        # the turn the instant it is accepted.
        session.add(SetupMessage(user_id=user_id, conversation_id=conversation_id,
                                 role="user", content=message, agent=agent))
        touch_conversation(session, user_id, conversation_id, message)
        reply_row = SetupMessage(user_id=user_id, conversation_id=conversation_id,
                                 role="assistant", content="", agent=agent, status="pending")
        session.add(reply_row)
    session.commit()

    tools = SetupTools(session, user_id)
    messages = history(session, user_id, conversation_id)
    prompt = system_prompt(agent)

    reply_parts: list[str] = []
    executed: list[dict] = []

    # **Streamed into the row as it arrives.** The page polls the transcript, so writing partial
    # text is what makes a two-minute turn feel answered rather than hung — and because it is the
    # same row the finished reply lands in, a reader who walks away and comes back sees the whole
    # thing rather than the half they watched.
    #
    # The live buffer is kept apart from `reply_parts` deliberately: `complete_tools` streams
    # chunks *and* returns the same text as its final value, so accumulating both would write
    # every reply twice. The buffer is what the reader watches; the returned text is what is kept.
    streaming: list[str] = []

    def _stream(chunk: str) -> None:
        streaming.append(chunk)
        reply_row.content = "\n\n".join([*reply_parts, "".join(streaming)]).strip()
        reply_row.status = "streaming"
        session.commit()
        if on_text is not None:
            on_text(chunk)

    for step in range(1, MAX_TOOL_ITERATIONS + 1):
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
    """Move the conversation to the top of the list, and name it if it has no name yet.

    The title is the first thing the user said, trimmed — no model call, because naming a chat is
    not worth a request that can fail, and the first sentence is what somebody would have typed
    anyway.
    """
    row = session.get(SetupConversation, conversation_id)
    if row is None or row.user_id != user_id:
        return
    row.last_message_at = func.now()
    if row.title == DEFAULT_TITLE:
        first = " ".join(message.split())
        row.title = (first[:57] + "…") if len(first) > 58 else first or DEFAULT_TITLE


def _written(executed: Sequence[dict]) -> list[str]:
    """Which files this turn actually saved — what the file panel needs to refresh."""
    return [call["input"]["name"] for call in executed
            if call["name"] == "write_profile_file" and "name" in (call["input"] or {})]
