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

from libs.agents.loop import run_turn
from libs.db.models import (
    MAX_CONVERSATIONS,
    SETUP_AGENTS,
    SetupConversation,
    SetupMessage,
    User,
)
from libs.llm import CLAUDE_KINDS, client_for
from libs.obs import audit
from libs.profiles import SEED_FILES
from libs.profiles.repository import ProfileFiles
from libs.queue import enqueue
from libs.secrets import SecretStore, redacting
from services.api.deps import current_user, get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/setup", tags=["setup"])

WRITEABLE = {name for name, _kind in SEED_FILES}


class Turn(BaseModel):
    message: str = Field(min_length=1, max_length=8_000)
    # Which command is driving. Defaults to the one that has to run first.
    agent: str = "setup"
    # Which chat this belongs to. Optional so an older client keeps working: absent means the most
    # recent conversation, which is what a single-threaded client always meant.
    conversation_id: _uuid.UUID | None = None


class FileEdit(BaseModel):
    content: str = Field(max_length=200_000)


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
        "messages": [{"role": row.role, "content": row.content, "agent": row.agent,
                      "files_written": _written(row.tool_calls),
                      "at": row.created_at.isoformat() if row.created_at else None}
                     for row in rows],
        "files": [{**row, "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None}
                  for row in files.listing()],
        "ready": files.is_ready(),
        "agents": list(SETUP_AGENTS),
    }


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


@router.post("/message")
def send(body: Turn, db: Session = Depends(get_db),
         user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """One turn: the user says something, the agent answers and writes what it learned.

    Synchronous, and a turn can take minutes on a frontier model. The client shows it as pending;
    an interview that arrives complete is better than one that arrives in pieces and then fails.

    **On the user's own credential**, like every other call this platform makes. Their profile,
    their spend, no platform cost.
    """
    if body.agent not in SETUP_AGENTS:
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

    store = SecretStore(db)
    for kind in CLAUDE_KINDS:
        with store.use(user_id, kind) as secret:
            if secret is None:
                continue
            value = secret.reveal()
            with redacting(value):
                result = run_turn(db, user_id, client=client_for(value, kind),
                                  message=body.message, agent=body.agent,
                                  conversation_id=current.id)

        files = ProfileFiles(db, user_id)
        audit(logger, "setup.turn", user=user_id, agent=body.agent,
              tools=len(result["tools_run"]), files=len(result["files_written"]))
        return {
            "conversation_id": str(current.id),
            "reply": result["reply"],
            "files_written": result["files_written"],
            "files": [{**row,
                       "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None}
                      for row in files.listing()],
            "ready": files.is_ready(),
            # Said, never acted on. Spending the user's money on a relabel is their decision
            # (`REBUILD_PLAN.md`, "Retraining is a notification, never a side effect").
            "scoring_changed": result["scoring_edits"],
        }

    raise HTTPException(
        status.HTTP_400_BAD_REQUEST,
        detail="Connect your Claude account first — setup runs on your own credential.")


@router.get("/files/{name}")
def read_file(name: str, db: Session = Depends(get_db),
              user_id: _uuid.UUID = Depends(current_user)) -> dict:
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
