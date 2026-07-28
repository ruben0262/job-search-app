"""Uploading the documents that "process data" will read.

Upload is deliberately cheap and deterministic: text is extracted in the request, the row is
written, and nothing else happens. No LLM runs here (`ONBOARDING_AND_PROCESSING.md` §1), so a user
can finish onboarding while Anthropic is down and it costs them nothing.

**The file itself is never written to disk.** Extraction happens in memory and the bytes are
discarded — see `libs.documents` for why that trade is the right way round.
"""
from __future__ import annotations

import logging
import uuid as _uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from libs.db.models import User, UserDocument
from libs.db.models.document import DOCUMENT_KINDS
from libs.documents import MAX_FILE_BYTES, SUPPORTED, UnsupportedDocument, extract_text
from libs.obs import audit
from services.api.deps import current_user, get_db, rate_limit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])

# Enough for a CV, a couple of covers, references, diplomas and a history file, with room to spare.
# A ceiling exists because the processing step reads all of them into one conversation.
MAX_DOCUMENTS_PER_USER = 25


def _documents_changed(db: Session, user_id: _uuid.UUID) -> None:
    """Mark the document set as changed, which is what makes the dashboard offer a re-process.

    A timestamp compared against `processed_at` rather than a "needs processing" flag: a flag has to
    be cleared by whoever remembers, and the first thing to go stale would be the one that says
    whether anything is stale (`ONBOARDING_AND_PROCESSING.md` §4).
    """
    db.get(User, user_id).documents_changed_at = func.now()


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload(request: Request, kind: str = Form(...), file: UploadFile = File(...),
                 db: Session = Depends(get_db),
                 user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Extract and store one document's text. Re-uploading the same content replaces it.

    Replacement rather than a duplicate row because users press the button twice, and two copies of
    one CV in the processing conversation is one CV's worth of signal at twice the cost.
    """
    if kind not in DOCUMENT_KINDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail=f"unknown document kind {kind!r}")
    rate_limit(request, f"upload:{user_id}", limit=60, per_seconds=3600)

    data = await file.read(MAX_FILE_BYTES + 1)
    try:
        extracted = extract_text(file.filename or "upload", data)
    except UnsupportedDocument as exc:
        # 422: the request was well-formed, the file was not. The message is written for the person
        # who uploaded it, not for a log.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    finally:
        # The bytes never reach disk and do not outlive the request.
        del data

    stored = db.scalar(
        select(UserDocument).where(UserDocument.user_id == user_id,
                                   UserDocument.text_sha256 == extracted.sha256))
    if stored is not None:
        stored.kind, stored.filename = kind, file.filename or stored.filename
        _documents_changed(db, user_id)
        audit(logger, "document.replaced", user=user_id, kind=kind, chars=extracted.chars)
        return {"status": "replaced", "id": stored.id, "chars": extracted.chars,
                "truncated": extracted.truncated}

    total = db.scalar(
        select(func.count()).select_from(UserDocument)
        .where(UserDocument.user_id == user_id)) or 0
    if total >= MAX_DOCUMENTS_PER_USER:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"you have {total} documents, the limit is {MAX_DOCUMENTS_PER_USER} — "
                   f"remove one first")

    document = UserDocument(user_id=user_id, kind=kind, filename=file.filename or "upload",
                            text=extracted.text, text_sha256=extracted.sha256,
                            chars=extracted.chars)
    db.add(document)
    db.flush()
    _documents_changed(db, user_id)
    audit(logger, "document.uploaded", user=user_id, kind=kind, chars=extracted.chars,
          truncated=extracted.truncated)
    return {"status": "stored", "id": document.id, "chars": extracted.chars,
            "truncated": extracted.truncated}


@router.get("")
def list_documents(db: Session = Depends(get_db),
                   user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """What the user has uploaded. Returns metadata and a short preview, never the whole text.

    The full text is only ever read by the processing step. Returning it here would put a person's
    complete employment history into a response that the dashboard has no use for.
    """
    rows = db.scalars(
        select(UserDocument).where(UserDocument.user_id == user_id)
        .order_by(UserDocument.uploaded_at)).all()
    return {
        "documents": [
            {"id": row.id, "kind": row.kind, "filename": row.filename, "chars": row.chars,
             "preview": row.text[:200],
             "uploaded_at": row.uploaded_at.isoformat() if row.uploaded_at else None}
            for row in rows],
        "supported": list(SUPPORTED),
        "limit": MAX_DOCUMENTS_PER_USER,
    }


@router.delete("/{document_id}")
def delete_document(document_id: int, db: Session = Depends(get_db),
                    user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Remove one document. Scoped to the caller, so an id from another account finds nothing."""
    document = db.scalar(
        select(UserDocument).where(UserDocument.id == document_id,
                                   UserDocument.user_id == user_id))
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such document")
    db.delete(document)
    _documents_changed(db, user_id)
    audit(logger, "document.deleted", user=user_id, kind=document.kind)
    return {"status": "deleted"}
