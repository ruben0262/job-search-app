"""Build your profile — the setup conversation, and the files it writes."""
from __future__ import annotations

import logging
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from libs import limits
from libs.agents import FILE_OWNERS, STARTABLE, UNOWNED_FILES
from libs.agents.loop import touch_conversation
from libs.db.models import (
    SETUP_AGENTS,
    SetupConversation,
    SetupMessage,
    User,
    UserSecret,
)
from libs.llm import CLAUDE_KINDS
from libs.obs import audit
from libs.profiles import WRITEABLE, setup_files
from libs.profiles.repository import ProfileFiles
from libs.queue import enqueue
from services.api.deps import current_user, get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/setup", tags=["setup"])



class Turn(BaseModel):
    message: str = Field(min_length=1, max_length=8_000)
    # Which command is driving.
    agent: str | None = None
    # Which chat this belongs to.
    conversation_id: _uuid.UUID | None = None


class FileEdit(BaseModel):
    content: str = Field(max_length=200_000)


def _stored_claude_kinds(db: Session, user_id: _uuid.UUID) -> set[str]:
    """Which Claude credentials this account has, if any."""
    stored = set(db.scalars(select(UserSecret.kind).where(UserSecret.user_id == user_id)).all())
    return stored & set(CLAUDE_KINDS)


def _conversations(db: Session, user_id: _uuid.UUID) -> list[SetupConversation]:
    """This account's chats, newest activity first."""
    return list(db.scalars(
        select(SetupConversation)
        .where(SetupConversation.user_id == user_id)
        .order_by(SetupConversation.last_message_at.desc().nulls_first(),
                  SetupConversation.created_at.desc())).all())


def _owned(db: Session, user_id: _uuid.UUID, conversation_id: _uuid.UUID) -> SetupConversation:
    """One chat, or 404. Ownership is checked here so no caller can forget to."""
    row = db.get(SetupConversation, conversation_id)
    if row is None or row.user_id != user_id:
        # 404 rather than 403: whether somebody else's conversation exists is not this caller's business.
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such conversation")
    return row


@router.get("")
def conversation(db: Session = Depends(get_db),
                 user_id: _uuid.UUID = Depends(current_user),
                 conversation_id: _uuid.UUID | None = None) -> dict:
    """One chat's transcript, the list of chats, the files, and whether documents can be built."""
    files = ProfileFiles(db, user_id)
    # Seeded here so an account created before this existed picks up the scaffolds on first open.
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
        # The account's own ceiling, not the module constant. `new_conversation` below enforces
        # `limits.resolve(...)`, so reporting `MAX_CONVERSATIONS` here told the page 3 while the
        # API would happily accept 10 — an operator raising the limit changed nothing the user
        # could see, because the page hides the control at the number it was given. `None` is
        # Unlimited and must stay `None`: a fallback to the constant is how "Unlimited" silently
        # became 3.
        "max_conversations": limits.resolve(db.get(User, user_id), "conversations"),
        # `status` tells the page whether to keep polling: `pending` or `streaming` is a turn still running.
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
    """Stop a slash command being delivered to an agent as ordinary text."""
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
    """Start a fresh chat, with an empty transcript and the same profile underneath."""
    existing = _conversations(db, user_id)
    # The admin panel's `conversations` limit. `None` is Unlimited, not "use the constant".
    ceiling = limits.resolve(db.get(User, user_id), "conversations")
    if ceiling is not None and len(existing) >= ceiling:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"You can keep {ceiling} chats. Delete one to start another.")

    row = SetupConversation(user_id=user_id)
    db.add(row)
    db.flush()
    audit(logger, "setup.conversation_created", user=user_id, conversation=row.id)
    return {"id": str(row.id), "title": row.title}


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: _uuid.UUID, db: Session = Depends(get_db),
                        user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Delete a chat and everything said in it."""
    row = _owned(db, user_id, conversation_id)
    db.delete(row)
    audit(logger, "setup.conversation_deleted", user=user_id, conversation=conversation_id)
    return {"status": "deleted"}



def _resolve_agent(db: Session, user_id: _uuid.UUID, conversation_id: _uuid.UUID,
                   named: str | None) -> str:
    """Which command handles this turn."""
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



class ConversationTitle(BaseModel):
    # Trimmed rather than rejected: truncating a name is kinder than refusing a save.
    title: str = Field(min_length=1, max_length=80)


@router.patch("/conversations/{conversation_id}")
def rename_conversation(conversation_id: _uuid.UUID, body: ConversationTitle,
                        db: Session = Depends(get_db),
                        user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Rename a chat."""
    row = _owned(db, user_id, conversation_id)
    row.title = " ".join(body.title.split())[:80]
    audit(logger, "setup.conversation_renamed", user=user_id, conversation=conversation_id)
    return {"id": str(row.id), "title": row.title}

@router.post("/message", status_code=status.HTTP_202_ACCEPTED)
def send(body: Turn, db: Session = Depends(get_db),
         user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Accept one turn and queue it."""
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

    # Which command is driving, once the conversation has one.
    agent = _resolve_agent(db, user_id, current.id, body.agent)
    _refuse_misdirected_command(body.message, agent)

    # Refused here rather than in the worker: the user can act on it the moment they pressed send.
    if not _stored_claude_kinds(db, user_id):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Connect your Claude account first — setup runs on your own credential.")

    # Both rows before the queue sees anything.
    db.add(SetupMessage(user_id=user_id, conversation_id=current.id,
                        role="user", content=body.message, agent=agent))
    touch_conversation(db, user_id, current.id, body.message)
    reply = SetupMessage(user_id=user_id, conversation_id=current.id,
                         role="assistant", content="", agent=agent, status="pending")
    db.add(reply)
    db.flush()

    # No dedup key: two questions in a row are two turns, and collapsing them would silently drop the second.
    enqueue(db, "setup", user_id, payload={"message_id": reply.id, "message": body.message,
                                           "agent": agent})
    audit(logger, "setup.turn_queued", user=user_id, agent=agent, conversation=current.id)
    return {"conversation_id": str(current.id), "message_id": reply.id, "status": "queued"}


@router.post("/message/{message_id}/stop")
def stop_turn(message_id: int, db: Session = Depends(get_db),
              user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Ask the running turn to stop."""
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
    """Is setup finished, what is left, and which command produces each missing piece."""
    files = ProfileFiles(db, user_id)
    # Repair first: `filled` is derived data in a column and has drifted in production.
    files.repair_filled_flags()

    missing = files.missing_setup()
    outstanding: dict[str, list[str]] = {}
    # Files nothing can produce are reported as such rather than attributed to a command.
    blocked = {name: UNOWNED_FILES[name] for name in missing if name in UNOWNED_FILES}
    for name in missing:
        if name in UNOWNED_FILES:
            continue
        outstanding.setdefault(FILE_OWNERS[name], []).append(name)

    return {
        "complete": not missing,
        "missing": missing,
        "required": list(setup_files()),
        # What the Start button runs, and it is the director whenever anything is missing.
        "next_command": "setup-director" if missing else None,
        "outstanding_by_command": outstanding,
        "blocked": blocked,
        "commands": list(STARTABLE),
        "may_train": not missing,
    }


def _require_files_visible(db: Session, user_id: _uuid.UUID) -> None:
    """Refuse unless this account has been opened by an operator."""
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
    """Edit a file by hand."""
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
    """Begin the first labelling run — the step after setup, pressed rather than assumed."""
    files = ProfileFiles(db, user_id)
    if not files.is_ready():
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="Finish building your profile first.")

    # Starting again clears a previous stop, or Stop would be permanent rather than a pause.
    user = db.get(User, user_id)
    if user is not None and user.labelling_paused:
        user.labelling_paused = False
        db.flush()
    enqueue(db, "label", user_id, dedup_key="label")
    db.commit()
    audit(logger, "setup.search_started", user=user_id)
    return {"status": "started",
            "message": "Scoring your first jobs. Your feed will fill in as it runs."}


def _written(tool_calls: list | None) -> list[str]:
    """Which files a turn saved — what the transcript shows under an assistant message."""
    return [call["input"]["name"] for call in tool_calls or []
            if call.get("name") == "write_profile_file" and "name" in (call.get("input") or {})]
