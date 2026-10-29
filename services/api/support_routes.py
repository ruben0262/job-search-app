"""The help chat on the site: ask, watch the answer arrive, and tap to make a change it offered.

`libs.assistant` writes each reply on a thread of its own. This file takes the question, streams the
reply's row back as Server-Sent Events, and makes a change only when the person taps the button the
reply offered — the one place a help-chat suggestion reaches an account, and only a person reaches it.
"""
from __future__ import annotations

import asyncio
import logging
import uuid as _uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from starlette.concurrency import run_in_threadpool

from libs import assistant, settings, suggestions
from libs.assistant import HISTORY, RUNNING, SUPPORT, TICK, AiStatus, ai_status, steps_of
from libs.contracts.config import UserConfigSchema, migrate_config
from libs.db.models import SetupConversation, SetupMessage, User
from libs.legal import has_consented
from libs.llm import PLATFORM, record_ai_request, resolve
from libs.matching import norm
from libs.obs import audit
from libs.tenant.repository import TenantRepository
from services.api.deps import current_user, get_db
from services.api.onboarding_routes import save_config
from services.api.suggestion_routes import SuggestionPick, accept

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/support", tags=["support"])


class SupportStep(BaseModel):
    label: str
    # What a lookup found, or what came of a tapped button.
    output: str | None = None
    error: bool = False
    # A button: "proposed" until it is tapped, then "done" or "failed".
    state: Literal["proposed", "done", "failed"] | None = None


class SupportMessage(BaseModel):
    id: int
    role: Literal["user", "assistant"]
    text: str
    status: Literal["pending", "streaming", "done", "failed"]
    # Why the reply failed, in the person's words.
    error: str | None
    steps: list[SupportStep]
    at: datetime | None


class SupportAi(BaseModel):
    # "own": their own key, unlimited. "platform": the daily allowance. None: nobody can pay now.
    payer: Literal["own", "platform"] | None
    provider: str | None
    # Requests left today; None is unlimited.
    left: int | None
    per_day: int


class SupportPage(BaseModel):
    # Why a question cannot be asked now and what to do about it; None when it can.
    closed: str | None
    ai: SupportAi
    # Whether answers may draw on what their documents say.
    consent: bool
    messages: list[SupportMessage]


class Question(BaseModel):
    text: str = Field(min_length=1, max_length=2_000)


class Asked(BaseModel):
    reply_id: int


class SupportEvent(BaseModel):
    """One event of a reply's stream. `snapshot` and `done` carry the reply so far, `delta` the text
    since, `step` one step at its place."""
    text: str | None = None
    status: str | None = None
    error: str | None = None
    steps: list[SupportStep] | None = None
    index: int | None = None
    step: SupportStep | None = None


@router.get("", response_model=SupportPage)
def page(db: Session = Depends(get_db), user_id: _uuid.UUID = Depends(current_user)) -> SupportPage:
    """The conversation so far, whether a question can be asked now, and what is left today."""
    ai = ai_status(db, db.get(User, user_id))
    rows = db.scalars(select(SetupMessage).where(
        SetupMessage.user_id == user_id, SetupMessage.agent == SUPPORT)
        .order_by(SetupMessage.id.desc()).limit(HISTORY)).all()
    return SupportPage(
        closed=_closed(db, ai), consent=has_consented(db, user_id),
        ai=SupportAi(payer=ai.payer, provider=ai.provider, left=ai.left, per_day=ai.per_day),
        messages=[SupportMessage(id=row.id, role=row.role, text=row.content, status=row.status,
                                 error=row.error, steps=steps_of(row), at=row.created_at)
                  for row in reversed(rows)])


@router.post("/messages", status_code=status.HTTP_202_ACCEPTED, response_model=Asked)
def ask(body: Question, db: Session = Depends(get_db),
        user_id: _uuid.UUID = Depends(current_user)) -> Asked:
    """Ask. The answer is written on its own; `/support/replies/{id}/stream` follows it."""
    user = db.get(User, user_id)
    payer = resolve(db, user)
    closed = _closed(db, ai_status(db, user))
    if closed or payer is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=closed)
    if db.scalar(select(SetupMessage.id).where(
            SetupMessage.user_id == user_id, SetupMessage.agent == SUPPORT,
            SetupMessage.status.in_(RUNNING)).limit(1)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail="Wait for the answer you are getting to finish first.")

    conversation = _conversation(db, user_id)
    db.add(SetupMessage(user_id=user_id, conversation_id=conversation, role="user",
                        content=body.text.strip(), agent=SUPPORT))
    reply = SetupMessage(user_id=user_id, conversation_id=conversation, role="assistant",
                         content="", agent=SUPPORT, status="pending",
                         tool_calls={"payer": payer.kind, "steps": []})
    db.add(reply)
    if payer.kind == PLATFORM:
        record_ai_request(db, user_id)
    # Before the writer starts: it reads the row on a connection of its own.
    db.commit()
    assistant.start(reply.id)
    audit(logger, "support.asked", user=user_id, reply=reply.id, payer=payer.kind)
    return Asked(reply_id=reply.id)


@router.get("/replies/{reply_id}/stream", response_model=SupportEvent,
            responses={200: {"content": {"text/event-stream": {}},
                             "description": "Server-Sent Events, each one's data a SupportEvent."}})
async def stream(reply_id: int, db: Session = Depends(get_db),
                 user_id: _uuid.UUID = Depends(current_user)) -> StreamingResponse:
    """A reply as it is written: `snapshot`, then `delta` and `step` as they come, then `done`."""
    row = await run_in_threadpool(_read, db, user_id, reply_id)
    if row.status in RUNNING:
        # Perhaps nobody is writing it: the worker that was is gone. The lock lets one start it.
        assistant.start(reply_id)
    return StreamingResponse(_events(db, user_id, reply_id, row), media_type="text/event-stream",
                             # A proxy in between would otherwise hold the stream back.
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def _events(db: Session, user_id: _uuid.UUID, reply_id: int, row):
    sent, steps = None, []
    while True:
        now = steps_of(row)
        if sent is None or not row.content.startswith(sent):
            # The first look, or a reply that started again after a restart.
            yield _event("snapshot", text=row.content, steps=now, status=row.status)
        else:
            if row.content != sent:
                yield _event("delta", text=row.content[len(sent):])
            for index, step in enumerate(now):
                if index >= len(steps) or steps[index] != step:
                    yield _event("step", index=index, step=step)
        sent, steps = row.content, now
        if row.status not in RUNNING:
            yield _event("done", text=row.content, status=row.status, error=row.error, steps=now)
            return
        await asyncio.sleep(TICK)
        row = await run_in_threadpool(_read, db, user_id, reply_id)


@router.post("/replies/{reply_id}/steps/{index}", response_model=SupportStep)
def tap(reply_id: int, index: int, db: Session = Depends(get_db),
        user_id: _uuid.UUID = Depends(current_user)) -> SupportStep:
    """Make the change a reply offered, because the person tapped its button. Once: a second tap
    is told how the first went."""
    row = db.scalar(select(SetupMessage).where(
        SetupMessage.id == reply_id, SetupMessage.user_id == user_id,
        SetupMessage.agent == SUPPORT).with_for_update())
    steps = steps_of(row) if row is not None else []
    if not 0 <= index < len(steps) or not steps[index].get("state"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That button is not there any more.")
    if row.status in RUNNING:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Wait for the answer to finish first.")
    step = steps[index]
    if step["state"] != "done":
        try:
            step["output"], step["state"] = _CHANGES[step["tool"]](db, user_id, step["args"]), "done"
        except HTTPException as exc:
            step["output"], step["state"] = str(exc.detail), "failed"
        flag_modified(row, "tool_calls")
        audit(logger, "support.tapped", user=user_id, reply=reply_id, change=step["tool"],
              state=step["state"])
    return SupportStep(**step)


def _closed(db: Session, ai: AiStatus) -> str | None:
    """Why a question cannot be asked now, and what to do about it; None when it can."""
    if not settings.get(db, "support_chat_enabled"):
        return "The help chat is not open yet."
    return ai.why_not


def _conversation(db: Session, user_id: _uuid.UUID) -> _uuid.UUID:
    """The help chat's conversation: the one its messages are in, or a new one for the first."""
    found = db.scalar(select(SetupMessage.conversation_id).where(
        SetupMessage.user_id == user_id, SetupMessage.agent == SUPPORT)
        .order_by(SetupMessage.id.desc()).limit(1))
    if found is None:
        conversation = SetupConversation(user_id=user_id, title="Help")
        db.add(conversation)
        db.flush()
        found = conversation.id
    return found


def _read(db: Session, user_id: _uuid.UUID, reply_id: int):
    row = db.execute(select(SetupMessage.content, SetupMessage.status, SetupMessage.error,
                            SetupMessage.tool_calls).where(
        SetupMessage.id == reply_id, SetupMessage.user_id == user_id,
        SetupMessage.agent == SUPPORT, SetupMessage.role == "assistant")).one_or_none()
    # Its own transaction each time, so the next read sees what was written since.
    db.commit()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such reply")
    return row


def _event(name: str, **data) -> str:
    return f"event: {name}\ndata: {SupportEvent(**data).model_dump_json(exclude_none=True)}\n\n"


# ── the changes a reply can offer, made when the person taps ───────────────────────────────────

def _add(db: Session, user_id: _uuid.UUID, args: dict) -> str:
    accept(SuggestionPick(term=args["word"], kind=args["kind"]), db, user_id)
    return "Added."


def _remove(db: Session, user_id: _uuid.UUID, args: dict) -> str:
    config = migrate_config(TenantRepository(db).get_config(user_id) or {"sections": []})
    for section in config["sections"]:
        for field in ("keywords", "exclude_keywords"):
            section[field] = [word for word in section.get(field) or [] if norm(word) != args["word"]]
    try:
        search = UserConfigSchema.model_validate(config)
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"“{args['word']}” cannot be removed: {exc.errors()[0]['msg']}") from exc
    save_config(db, user_id, search)
    return "Removed."


def _stop(db: Session, user_id: _uuid.UUID, args: dict) -> str:
    suggestions.dismiss(db, user_id, args["word"])
    return "We will not suggest it again."


_CHANGES = {"add_word": _add, "remove_word": _remove, "stop_suggesting": _stop}
