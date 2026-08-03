"""Build your profile — the setup conversation, and the files it writes.

This replaces "process your documents". That step spent one Opus call on a schema and produced a
profile with no career in it; this is a conversation with the reference instance's own setup
agents, which ask the questions somebody would ask and write the files the rest of the platform
reads (`REBUILD_PLAN.md`).

**The user never sees the file machinery.** They answer questions about themselves. That the
answers become eight markdown files is this module's business, not theirs — so the endpoints are
named for the conversation, and the file panel is there for anyone who wants to look rather than
because they have to.

**Nothing here starts work that costs money.** Finishing setup does not begin labelling: the reply
says what the next step is and the user presses it. The one exception in the old flow — confirming
a profile silently queueing a relabel — is exactly the surprise this avoids.
"""
from __future__ import annotations

import logging
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from libs.agents import FILE_OWNERS, STARTABLE, UNOWNED_FILES
from libs.agents.loop import touch_conversation
from libs.db.models import (
    MAX_CONVERSATIONS,
    SETUP_AGENTS,
    SetupConversation,
    SetupMessage,
    User,
    UserSecret,
)
from libs.llm import CLAUDE_KINDS
from libs.obs import audit
from libs.profiles import SEED_FILES, setup_files
from libs.profiles.repository import ProfileFiles
from libs.queue import enqueue
from services.api.deps import current_user, get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/setup", tags=["setup"])

WRITEABLE = {name for name, _kind in SEED_FILES}


class Turn(BaseModel):
    message: str = Field(min_length=1, max_length=8_000)
    # Which command is driving. **Absent means "whatever this conversation is already running"**,
    # resolved below; only a message that opens with a slash command names one.
    agent: str | None = None
    # Which chat this belongs to. Optional so an older client keeps working: absent means the most
    # recent conversation, which is what a single-threaded client always meant.
    conversation_id: _uuid.UUID | None = None


class FileEdit(BaseModel):
    content: str = Field(max_length=200_000)


def _stored_claude_kinds(db: Session, user_id: _uuid.UUID) -> set[str]:
    """Which Claude credentials this account has, if any."""
    stored = set(db.scalars(select(UserSecret.kind).where(UserSecret.user_id == user_id)).all())
    return stored & set(CLAUDE_KINDS)


def _conversations(db: Session, user_id: _uuid.UUID) -> list[SetupConversation]:
    """This account's chats, newest activity first. A brand-new chat has no activity yet, so
    `created_at` breaks the tie rather than leaving it to the database's mood."""
    return list(db.scalars(
        select(SetupConversation)
        .where(SetupConversation.user_id == user_id)
        .order_by(SetupConversation.last_message_at.desc().nulls_first(),
                  SetupConversation.created_at.desc())).all())


def _owned(db: Session, user_id: _uuid.UUID, conversation_id: _uuid.UUID) -> SetupConversation:
    """One chat, or 404. Ownership is checked here so no caller can forget to."""
    row = db.get(SetupConversation, conversation_id)
    if row is None or row.user_id != user_id:
        # 404 rather than 403: whether somebody else's conversation exists is not this caller's
        # business to learn.
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such conversation")
    return row


@router.get("")
def conversation(db: Session = Depends(get_db),
                 user_id: _uuid.UUID = Depends(current_user),
                 conversation_id: _uuid.UUID | None = None) -> dict:
    """One chat's transcript, the list of chats, the files, and whether documents can be built.

    **The chat list and the open chat come back together** because they are always rendered
    together, and two calls would let the switcher disagree with the transcript beneath it.

    With no `conversation_id` this opens the most recent chat, creating the first one if the
    account has none — so a new user lands in a conversation without pressing anything.
    """
    files = ProfileFiles(db, user_id)
    # Seeding here rather than at signup so an account created before this existed picks up the
    # scaffolds the first time it opens the page — one fewer migration to write and to remember.
    files.seed()

    existing = _conversations(db, user_id)
    if conversation_id is not None:
        current = _owned(db, user_id, conversation_id)
    elif existing:
        current = existing[0]
    else:
        current = SetupConversation(user_id=user_id)
        db.add(current)
        db.flush()
        existing = [current]
    db.commit()

    rows = db.scalars(
        select(SetupMessage)
        .where(SetupMessage.user_id == user_id,
               SetupMessage.conversation_id == current.id)
        .order_by(SetupMessage.id)).all()

    return {
        "conversation_id": str(current.id),
        "conversations": [{"id": str(row.id), "title": row.title,
                           "created_at": row.created_at.isoformat() if row.created_at else None,
                           "last_message_at": (row.last_message_at.isoformat()
                                               if row.last_message_at else None)}
                          for row in existing],
        "max_conversations": MAX_CONVERSATIONS,
        # `status` is what tells the page whether to keep polling: a `pending` or `streaming` row
        # is a turn still running somewhere, and it renders as the thinking state.
        # `id` so the page can name the running turn when the user presses stop.
        "messages": [{"id": row.id, "role": row.role, "content": row.content, "agent": row.agent,
                      "status": row.status, "error": row.error,
                      "files_written": _written(row.tool_calls),
                      "at": row.created_at.isoformat() if row.created_at else None}
                     for row in rows],
        # The tiles are still listed when this is false -- what closes is opening one.
        "files_visible": bool(db.get(User, user_id).profile_files_visible),
        "files": [{**row, "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None}
                  for row in files.listing()],
        "ready": files.is_ready(),
        "agents": list(SETUP_AGENTS),
    }



def _refuse_misdirected_command(message: str, agent: str) -> None:
    """Stop a slash command being delivered to an agent as ordinary text.

    **A conversation runs one command, and a typed command was just words.** It reached the model as
    a user message, which then did the reasonable thing with an instruction it could not carry out:
    improvised. In a live run `/setup-boards` was typed into a `/setup` chat and the agent invented
    a destination for it, announcing that it would write the search configuration into two profile
    files instead -- files nothing reads as search configuration. Nothing was refused, nothing
    errored, and the user was told a plausible sentence about work that could not happen.

    So the command never gets that far. Three cases, all answered here rather than by the model:

    - the conversation's own command, which is how a chat is kicked off, passes;
    - another command this platform can run, which needs its own chat;
    - a command this platform does not have, which is said plainly rather than improvised around.

    Ported prompts mention commands that only ever existed on the single-tenant instance --
    `/setup-boards`, `/setup-companies`, `/apply` -- so a user reading one will type them, and they
    have to land somewhere honest.
    """
    text = message.strip()
    if not text.startswith("/"):
        return
    name = text[1:].split()[0].strip().lower() if len(text) > 1 else ""
    if not name or name == agent:
        return

    if name in SETUP_AGENTS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(f"/{name} runs in its own chat. Start a new chat and choose it there, and "
                    "this conversation will still be here when you come back."))
    raise HTTPException(
        status.HTTP_400_BAD_REQUEST,
        detail=(f"There is no /{name} on this platform. What jobs to search for is set on your "
                "Settings page rather than in a conversation."))

@router.post("/conversations", status_code=status.HTTP_201_CREATED)
def new_conversation(db: Session = Depends(get_db),
                     user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Start a fresh chat, with an empty transcript and the same profile underneath.

    **The cap is enforced here and is not a setting** (owner, 2026-07-30). Every turn replays its
    whole conversation on the user's own credential, so unbounded chats is unbounded spend on
    re-reading them. Hitting the cap is answered with what to do about it rather than a bare
    refusal, because deleting one is the intended move.
    """
    existing = _conversations(db, user_id)
    if len(existing) >= MAX_CONVERSATIONS:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"You can keep {MAX_CONVERSATIONS} chats. Delete one to start another.")

    row = SetupConversation(user_id=user_id)
    db.add(row)
    db.flush()
    audit(logger, "setup.conversation_created", user=user_id, conversation=row.id)
    return {"id": str(row.id), "title": row.title}


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: _uuid.UUID, db: Session = Depends(get_db),
                        user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Delete a chat and everything said in it. **The profile is untouched.**

    That asymmetry is the point and is worth stating: the files are what the conversation was for,
    and throwing away the talking must not throw away the work. Deleting the last chat is allowed —
    the next visit opens a fresh one, which is the same place a new account starts.
    """
    row = _owned(db, user_id, conversation_id)
    db.delete(row)
    audit(logger, "setup.conversation_deleted", user=user_id, conversation=conversation_id)
    return {"status": "deleted"}



def _resolve_agent(db: Session, user_id: _uuid.UUID, conversation_id: _uuid.UUID,
                   named: str | None) -> str:
    """Which command handles this turn.

    A named one wins: that is a user opening a chat with a slash command. Otherwise the conversation
    keeps whatever it has been running, because a conversation is one command from end to end --
    that is the assumption the whole design rests on, and it was the one thing not being enforced.

    `setup` remains the fallback for a chat that has never had an agent, which is what a client
    sending a bare message has always meant.
    """
    if named is not None:
        return named
    existing = db.scalar(
        select(SetupMessage.agent)
        .where(SetupMessage.user_id == user_id,
               SetupMessage.conversation_id == conversation_id,
               SetupMessage.agent.isnot(None))
        .order_by(SetupMessage.id)
        .limit(1))
    return existing or "setup"


@router.post("/message", status_code=status.HTTP_202_ACCEPTED)
def send(body: Turn, db: Session = Depends(get_db),
         user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Accept one turn and queue it. Answers immediately; the reply arrives in the transcript.

    **This used to run the turn inside the request, and that was the bug.** A turn takes minutes on
    a frontier model, so switching tabs, opening another chat, refreshing or signing out aborted
    it — the user's message was committed, the reply never came, and nothing said why. Now the
    pending assistant row is created here and `services/setupagent` fills it in, so a turn survives
    everything the browser can do to it.

    **On the user's own credential**, like every other call this platform makes. Their profile,
    their spend, no platform cost.
    """
    if body.agent is not None and body.agent not in SETUP_AGENTS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"no such step: {body.agent}")

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")

    if body.conversation_id is not None:
        current = _owned(db, user_id, body.conversation_id)
    else:
        existing = _conversations(db, user_id)
        current = existing[0] if existing else None
        if current is None:
            current = SetupConversation(user_id=user_id)
            db.add(current)
            db.flush()

    # Refused here rather than in the worker: "connect your Claude account" is an answer the user
    # can act on at the moment they pressed send, not a failed turn they find in the transcript.
    if not _stored_claude_kinds(db, user_id):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Connect your Claude account first — setup runs on your own credential.")

    # **Which command is driving, once the conversation has one.**
    #
    # Only a message that opens with a slash command names an agent; every ordinary reply omits it,
    # and the field used to default to `setup`. So a chat started as the director ran the director
    # for exactly one turn, and every answer the user typed after that was handled by `/setup` --
    # a prompt that knows nothing about the four-step map and is told, correctly for itself, to send
    # the user to a new chat for anything it cannot do. That is why the director kept handing off:
    # after the first turn it was not the director any more.
    agent = _resolve_agent(db, user_id, current.id, body.agent)
    _refuse_misdirected_command(body.message, agent)

    # **Both rows before the queue sees anything.** The page renders the question and a thinking
    # reply the instant this returns, and the worker has a row to stream into rather than one to
    # invent.
    db.add(SetupMessage(user_id=user_id, conversation_id=current.id,
                        role="user", content=body.message, agent=agent))
    touch_conversation(db, user_id, current.id, body.message)
    reply = SetupMessage(user_id=user_id, conversation_id=current.id,
                         role="assistant", content="", agent=agent, status="pending")
    db.add(reply)
    db.flush()

    # No dedup key: two questions in a row are two turns, and collapsing them would silently drop
    # the second. The per-conversation guard is the pending row itself — the client will not send
    # again while one is open.
    enqueue(db, "setup", user_id, payload={"message_id": reply.id, "message": body.message,
                                           "agent": agent})
    audit(logger, "setup.turn_queued", user=user_id, agent=agent, conversation=current.id)
    return {"conversation_id": str(current.id), "message_id": reply.id, "status": "queued"}


@router.post("/message/{message_id}/stop")
def stop_turn(message_id: int, db: Session = Depends(get_db),
              user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Ask the running turn to stop.

    **A request, not a kill.** The turn is on a worker that is usually mid-call to Anthropic, and
    that call cannot be interrupted without abandoning a response already paid for. So this flips
    the row to `cancelling` and the worker notices between tool steps, where everything so far is
    committed and the next call has not been made. The user sees "stopping…" until it does, which
    is honest — claiming it had stopped while it was still writing files would be a lie the file
    panel would immediately contradict.

    Idempotent, and safe on a turn that has already finished: a stop arriving one second after the
    reply landed is the normal race, not an error, and answering it with a 409 would put a red
    banner on a screen where nothing went wrong.
    """
    row = db.get(SetupMessage, message_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such message")

    if row.status in ("done", "failed", "cancelled"):
        return {"status": row.status, "detail": "That turn had already finished."}

    row.status = "cancelling"
    audit(logger, "setup.turn_stop_requested", user=user_id, message=message_id)
    return {"status": "cancelling", "detail": "Stopping — it will finish the step it is on."}


@router.get("/progress")
def setup_progress(db: Session = Depends(get_db),
                   user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Is setup finished, what is left, and which command produces each missing piece.

    **The success criterion lives here rather than in a prompt.** An agent asked to decide whether
    it has finished will either stop early or polish forever, and the ported director's gates read
    a filesystem this platform does not have. This answers from the database: a file exists and is
    filled, or it does not.

    It is also what unblocks training. `train_user` refuses while anything here is outstanding, so
    "setup complete" and "may train" are the same sentence rather than two rules that can disagree.
    """
    files = ProfileFiles(db, user_id)
    # Repair first: `filled` is derived data in a column and has drifted in production, and a user
    # being told to redo finished work is worse than the cost of recomputing it on a page load.
    files.repair_filled_flags()

    missing = files.missing_setup()
    outstanding: dict[str, list[str]] = {}
    # Files nothing can currently produce are reported as such rather than attributed to a command
    # that would not fix them — being told to run the wrong thing is worse than being told the truth.
    blocked = {name: UNOWNED_FILES[name] for name in missing if name in UNOWNED_FILES}
    for name in missing:
        if name in UNOWNED_FILES:
            continue
        outstanding.setdefault(FILE_OWNERS[name], []).append(name)

    return {
        "complete": not missing,
        "missing": missing,
        "required": list(setup_files()),
        # **What the Start button runs, and it is the director whenever anything is missing.**
        #
        # This used to pick the first command that owned an outstanding file, which could never be
        # the director: it owns none of them, because it writes nothing itself. So the one button a
        # new user presses started a single step, and finishing that step handed them a button for
        # the next one — four chats to do what one now does, which is the thing the director exists
        # to avoid.
        #
        # It is right for a partly-finished profile too. The director reads the file state before it
        # says anything and starts at the first outstanding step, so pressing Start after stopping
        # halfway resumes rather than repeats. `outstanding_by_command` below still says which step
        # owns what, for anyone who wants to run one on its own.
        "next_command": "setup-director" if missing else None,
        "outstanding_by_command": outstanding,
        "blocked": blocked,
        "commands": list(STARTABLE),
        "may_train": not missing,
    }


def _require_files_visible(db: Session, user_id: _uuid.UUID) -> None:
    """Refuse unless this account has been opened by an operator.

    **On the endpoint, not only on the page.** A flag that greyed the tiles while this still
    returned the contents would be a curtain: the URL is guessable, the response is JSON, and
    anyone who had the page open before it was closed would keep reading.
    """
    user = db.get(User, user_id)
    if user is None or not user.profile_files_visible:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="These are not open for reading yet. Ask the operator if you need them.")


@router.get("/files/{name}")
def read_file(name: str, db: Session = Depends(get_db),
              user_id: _uuid.UUID = Depends(current_user)) -> dict:
    _require_files_visible(db, user_id)
    content = ProfileFiles(db, user_id).read(name)
    if content is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")
    return {"name": name, "content": content}


@router.put("/files/{name}")
def write_file(name: str, body: FileEdit, db: Session = Depends(get_db),
               user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Edit a file by hand.

    Editing is allowed but the agent is the better route, especially for a prompt: it knows where a
    new role belongs and what the surrounding structure expects. This exists because somebody who
    spots a wrong date should be able to fix it in ten seconds.
    """
    # Editing implies reading: the form is filled with the current contents before it is saved.
    _require_files_visible(db, user_id)
    if name not in WRITEABLE:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")
    if not body.content.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="That would empty the file. Delete the section instead.")

    scoring = ProfileFiles(db, user_id).write(name, body.content)
    db.commit()
    audit(logger, "setup.file_edited", user=user_id, file=name, scoring_relevant=scoring)
    return {"name": name, "scoring_changed": [name] if scoring else []}


@router.post("/start")
def start_search(db: Session = Depends(get_db),
                 user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Begin the first labelling run — the step after setup, pressed rather than assumed.

    The old flow queued this the moment a profile was confirmed. It is the most expensive thing the
    platform does on somebody's behalf, and it should not begin because a form was submitted.
    """
    files = ProfileFiles(db, user_id)
    if not files.is_ready():
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="Finish building your profile first.")

    enqueue(db, "label", user_id, dedup_key="label")
    db.commit()
    audit(logger, "setup.search_started", user=user_id)
    return {"status": "started",
            "message": "Scoring your first jobs. Your feed will fill in as it runs."}


def _written(tool_calls: list | None) -> list[str]:
    """Which files a turn saved — what the transcript shows under an assistant message."""
    return [call["input"]["name"] for call in tool_calls or []
            if call.get("name") == "write_profile_file" and "name" in (call.get("input") or {})]
