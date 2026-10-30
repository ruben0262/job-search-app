"""A person's own documents: uploading them, and the CVs and cover letters built from them."""
from __future__ import annotations

import logging
import uuid as _uuid
from typing import Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel
from pymongo.database import Database

from libs import limits, suggestions
from libs.db import find, find_one, get, insert, now
from libs.db.models import BuiltDocument, CorpusJob, QueueJob, User, UserDocument
from libs.db.models.document import DOCUMENT_KINDS
from libs.documents import (
    MAX_FILE_BYTES,
    MAX_TEXT_CHARS,
    SUPPORTED,
    UnsupportedDocument,
    extract_text,
)
from libs.legal import has_consented
from libs.llm import PLATFORM, record_ai_request, resolve
from libs.obs import audit
from libs.profiles.repository import ProfileFiles
from libs.queue import enqueue
from services.api.deps import current_user, get_db, rate_limit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])


class StoredDocument(BaseModel):
    status: str
    id: int
    chars: int
    truncated: bool
    # Set when the file was longer than we keep, written for the person who uploaded it.
    note: str | None


class DocumentRow(BaseModel):
    id: int
    kind: str
    filename: str
    chars: int
    preview: str
    uploaded_at: str | None


class DocumentList(BaseModel):
    documents: list[DocumentRow]
    supported: list[str]


def _stored(status_word: str, document_id: int, extracted) -> dict:
    """One upload's answer, with the warning written for the person rather than for the model.

    A file longer than the ceiling is kept, not refused: the user decides whether to shorten it and
    upload again. But they have to be told, at the moment they can act on it — the previous
    behaviour reported the truncation into the setup agent's tool result, where the only reader was
    the model, and the person went on believing the whole file had been read.
    """
    note = None
    if extracted.truncated:
        note = (f"This file is longer than we read. We kept the first {MAX_TEXT_CHARS:,} characters "
                f"and everything after that was not stored. You can carry on, or shorten the file "
                f"to the parts that matter most and upload it again.")
    return {"status": status_word, "id": document_id, "chars": extracted.chars,
            "truncated": extracted.truncated, "note": note}


def _documents_changed(db: Database, user_id: _uuid.UUID) -> None:
    """Mark the document set as changed, and work their suggested words out again from it."""
    db[User.COLLECTION].update_one({"_id": user_id}, {"$set": {"documents_changed_at": now()}})
    suggestions.refresh(db, user_id)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=StoredDocument)
async def upload(request: Request, kind: str = Form(...), file: UploadFile = File(...),
                 db: Database = Depends(get_db),
                 user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Extract and store one document's text."""
    if kind not in DOCUMENT_KINDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail=f"unknown document kind {kind!r}")
    rate_limit(request, f"upload:{user_id}", limit=60, per_seconds=3600)

    data = await file.read(MAX_FILE_BYTES + 1)
    try:
        extracted = extract_text(file.filename or "upload", data)
    except UnsupportedDocument as exc:
        # 422: the request was well-formed, the file was not.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    finally:
        # The bytes never reach disk and do not outlive the request.
        del data

    documents = db[UserDocument.COLLECTION]
    stored = find_one(db, UserDocument, {"user_id": user_id, "text_sha256": extracted.sha256})
    if stored is not None:
        documents.update_one({"_id": stored.id}, {"$set": {"kind": kind,
                                                           "filename": file.filename or stored.filename}})
        _documents_changed(db, user_id)
        audit(logger, "document.replaced", user=user_id, kind=kind, chars=extracted.chars,
              truncated=extracted.truncated)
        return _stored("replaced", stored.id, extracted)

    if kind == "history":
        # The history is one document, always (owner, 2026-07-30).
        existing = find_one(db, UserDocument, {"user_id": user_id, "kind": "history"})
        if existing is not None:
            documents.update_one({"_id": existing.id}, {"$set": {
                "filename": file.filename or existing.filename, "text": extracted.text,
                "text_sha256": extracted.sha256, "chars": extracted.chars}})
            _documents_changed(db, user_id)
            audit(logger, "document.replaced", user=user_id, kind=kind, chars=extracted.chars,
                  truncated=extracted.truncated)
            return _stored("replaced", existing.id, extracted)

    total = documents.count_documents({"user_id": user_id})
    # The operator's ceiling.
    ceiling = limits.resolve(get(db, User, user_id), "documents")
    if ceiling is not None and total >= ceiling:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"you have {total} documents, the limit is {ceiling} — "
                   f"remove one first")

    document = insert(db, UserDocument(user_id=user_id, kind=kind, filename=file.filename or "upload",
                                       text=extracted.text, text_sha256=extracted.sha256,
                                       chars=extracted.chars))
    _documents_changed(db, user_id)
    audit(logger, "document.uploaded", user=user_id, kind=kind, chars=extracted.chars,
          truncated=extracted.truncated)
    return _stored("stored", document.id, extracted)


@router.get("", response_model=DocumentList)
def list_documents(db: Database = Depends(get_db),
                   user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """What the user has uploaded."""
    rows = find(db, UserDocument, {"user_id": user_id}, sort=[("uploaded_at", 1), ("_id", 1)])
    return {
        "documents": [
            {"id": row.id, "kind": row.kind, "filename": row.filename, "chars": row.chars,
             "preview": row.text[:200],
             "uploaded_at": row.uploaded_at.isoformat() if row.uploaded_at else None}
            for row in rows],
        "supported": list(SUPPORTED),
    }


@router.delete("/{document_id}")
def delete_document(document_id: int, db: Database = Depends(get_db),
                    user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Remove one document. Scoped to the caller, so an id from another account finds nothing."""
    document = find_one(db, UserDocument, {"_id": document_id, "user_id": user_id})
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such document")
    db[UserDocument.COLLECTION].delete_one({"_id": document.id})
    _documents_changed(db, user_id)
    audit(logger, "document.deleted", user=user_id, kind=document.kind)
    return {"status": "deleted"}


# ── a CV or cover letter for one job ──────────────────────────────────────────────────────────
#
# Downloaded here, rather than sent to Telegram. **The build is the docgen queue row**: its id is the
# build's id and its state is the build's state, so there is no second record to disagree with the
# first. The PDF waits in `built_documents` until the worker deletes it (`document_keep_hours`, D9).


class BuildRequest(BaseModel):
    job_id: int
    kind: Literal["cv", "cover"]
    # "instant" is built by code, free for everyone; "ai" is written by a model on a key that pays.
    mode: Literal["instant", "ai"]


class BuildStatus(BaseModel):
    id: int
    state: Literal["queued", "building", "ready", "failed", "expired"]
    # Why it failed or was refused, written for the person.
    detail: str | None = None
    # Set once `state` is "ready".
    download_url: str | None = None


@router.post("/build", status_code=status.HTTP_202_ACCEPTED, response_model=BuildStatus)
def build_document(body: BuildRequest, db: Database = Depends(get_db),
                   user_id: _uuid.UUID = Depends(current_user)) -> BuildStatus:
    """Queue one CV or cover letter for one job.

    Asked again while it waits, it is the same build, answered before any check: a second click must
    not be refused for the allowance its own first click used.
    """
    if not db[CorpusJob.COLLECTION].count_documents({"_id": body.job_id}, limit=1):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="that job is no longer here")
    key = f"{body.mode}:{body.kind}:{body.job_id}"
    build_id = _waiting(db, user_id, key)
    if build_id is None:
        user = get(db, User, user_id)
        allowed = limits.resolve(user, "cv_builds_per_day")
        if allowed is not None and _documents_today(db, user_id) >= allowed:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                detail=f"you have had your {allowed} documents for today — the "
                                       "count starts again at midnight")
        payload = {"job_id": body.job_id, "kind": body.kind, "mode": body.mode}
        if body.mode == "ai":
            payload["payer"] = _ai_payer(db, user)
        queued = enqueue(db, "docgen", user_id, payload=payload, dedup_key=key)
        if queued is not None and payload.get("payer") == PLATFORM:
            record_ai_request(db, user_id)
        # `None` from `enqueue` is a twin request that won the race, or an operator's pause.
        build_id = queued or _waiting(db, user_id, key)
        if build_id is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail="building documents is paused for a moment — try again soon")
        audit(logger, "documents.build_requested", user=user_id, job=body.job_id,
              kind=body.kind, mode=body.mode, payer=payload.get("payer"))
    return _status(db, get(db, QueueJob, build_id))


@router.get("/build/{build_id}", response_model=BuildStatus)
def build_status(build_id: int, db: Database = Depends(get_db),
                 user_id: _uuid.UUID = Depends(current_user)) -> BuildStatus:
    """Where one build is. The page asks until it is ready or has failed."""
    build = find_one(db, QueueJob, {"_id": build_id, "user_id": user_id, "lane": "docgen"})
    if build is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such build")
    return _status(db, build)


@router.get("/build/{build_id}/file", response_class=Response,
            responses={200: {"content": {"application/pdf": {}}, "description": "The PDF."}})
def build_file(build_id: int, db: Database = Depends(get_db),
               user_id: _uuid.UUID = Depends(current_user)) -> Response:
    """The finished PDF, while it is kept. Scoped to the caller, like everything here."""
    owned = db[QueueJob.COLLECTION].count_documents({"_id": build_id, "user_id": user_id}, limit=1)
    built = get(db, BuiltDocument, build_id) if owned else None
    if built is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            detail="this document is not ready, or was deleted — build it again")
    return Response(built.content, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{built.filename}"'})


def _documents_today(db: Database, user_id: _uuid.UUID) -> int:
    """Every document asked for since their midnight, instant or AI, whatever became of it."""
    return db[QueueJob.COLLECTION].count_documents(
        {"lane": "docgen", "user_id": user_id, "created_at": {"$gte": limits.their_midnight(db, user_id)}})


def _waiting(db: Database, user_id: _uuid.UUID, key: str) -> int | None:
    """The same build, asked for before and not yet finished."""
    found = db[QueueJob.COLLECTION].find_one(
        {"lane": "docgen", "user_id": user_id, "dedup_key": key, "state": {"$in": ["ready", "claimed"]}},
        {"_id": 1})
    return found["_id"] if found else None


def _ai_payer(db: Database, user: User) -> str:
    """Who pays for an AI-written document, once this person may have one at all.

    Refused here rather than queued and refused there: the worker asks again before any text leaves
    the server, but the person should not wait for a queue to hear "no".
    """
    if not has_consented(db, user.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="agree to how AI-written documents use your profile first — "
                                   "it is on the privacy page")
    if ProfileFiles(db, user.id).build_blocker() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail="AI-written documents need a writing profile this account does "
                                   "not have yet — the instant version is ready now")
    payer = resolve(db, user)
    if payer is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail="AI writing needs your own Claude, Gemini or OpenAI key — add "
                                   "one in settings, or use the instant version, which is free")
    return payer.kind


def _status(db: Database, build: QueueJob) -> BuildStatus:
    """The queue row, said the way the page shows it."""
    refused = (build.payload or {}).get("refused")
    if build.state == "dead" or refused:
        return BuildStatus(id=build.id, state="failed", detail=refused or build.last_error)
    if build.state != "done":
        return BuildStatus(id=build.id, state="building" if build.state == "claimed" else "queued")
    if not db[BuiltDocument.COLLECTION].count_documents({"_id": build.id}, limit=1):
        return BuildStatus(id=build.id, state="expired",
                           detail="this document has been deleted, as they all are after a while "
                                  "— build it again")
    return BuildStatus(id=build.id, state="ready", download_url=f"/documents/build/{build.id}/file")
