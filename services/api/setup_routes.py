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
from libs.db.models import SETUP_AGENTS, SetupMessage, User
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


class FileEdit(BaseModel):
    content: str = Field(max_length=200_000)


@router.get("")
def conversation(db: Session = Depends(get_db),
                 user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """The transcript, the files, and whether this account can generate documents yet."""
    files = ProfileFiles(db, user_id)
    # Seeding here rather than at signup so an account created before this existed picks up the
    # scaffolds the first time it opens the page — one fewer migration to write and to remember.
    files.seed()
    db.commit()

    rows = db.scalars(
        select(SetupMessage)
        .where(SetupMessage.user_id == user_id)
        .order_by(SetupMessage.id)).all()

    return {
        "messages": [{"role": row.role, "content": row.content, "agent": row.agent,
                      "files_written": _written(row.tool_calls),
                      "at": row.created_at.isoformat() if row.created_at else None}
                     for row in rows],
        "files": [{**row, "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None}
                  for row in files.listing()],
        "ready": files.is_ready(),
        "agents": list(SETUP_AGENTS),
    }


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

    store = SecretStore(db)
    for kind in CLAUDE_KINDS:
        with store.use(user_id, kind) as secret:
            if secret is None:
                continue
            value = secret.reveal()
            with redacting(value):
                result = run_turn(db, user_id, client=client_for(value, kind),
                                  message=body.message, agent=body.agent)

        files = ProfileFiles(db, user_id)
        audit(logger, "setup.turn", user=user_id, agent=body.agent,
              tools=len(result["tools_run"]), files=len(result["files_written"]))
        return {
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
