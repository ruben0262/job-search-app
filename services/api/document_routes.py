"""Uploading the documents that "process data" will read."""
from __future__ import annotations

import logging
import uuid as _uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from libs import limits
from libs.db.models import User, UserDocument
from libs.db.models.document import DOCUMENT_KINDS
from libs.documents import (
    MAX_FILE_BYTES,
    MAX_TEXT_CHARS,
    SUPPORTED,
    UnsupportedDocument,
    extract_text,
)
from libs.obs import audit
from services.api.deps import current_user, get_db, rate_limit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])

# Enough for a CV, a couple of covers, references, diplomas and a history file, with room to spare.
MAX_DOCUMENTS_PER_USER = 25


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


def _documents_changed(db: Session, user_id: _uuid.UUID) -> None:
    """Mark the document set as changed, which is what makes the dashboard offer a re-process."""
    db.get(User, user_id).documents_changed_at = func.now()


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload(request: Request, kind: str = Form(...), file: UploadFile = File(...),
                 db: Session = Depends(get_db),
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

    stored = db.scalar(
        select(UserDocument).where(UserDocument.user_id == user_id,
                                   UserDocument.text_sha256 == extracted.sha256))
    if stored is not None:
        stored.kind, stored.filename = kind, file.filename or stored.filename
        _documents_changed(db, user_id)
        audit(logger, "document.replaced", user=user_id, kind=kind, chars=extracted.chars,
              truncated=extracted.truncated)
        return _stored("replaced", stored.id, extracted)

    if kind == "history":
        # The history is one document, always (owner, 2026-07-30).
        existing = db.scalar(
            select(UserDocument).where(UserDocument.user_id == user_id,
                                       UserDocument.kind == "history"))
        if existing is not None:
            existing.filename = file.filename or existing.filename
            existing.text, existing.text_sha256 = extracted.text, extracted.sha256
            existing.chars = extracted.chars
            _documents_changed(db, user_id)
            audit(logger, "document.replaced", user=user_id, kind=kind, chars=extracted.chars,
                  truncated=extracted.truncated)
            return _stored("replaced", existing.id, extracted)

    total = db.scalar(
        select(func.count()).select_from(UserDocument)
        .where(UserDocument.user_id == user_id)) or 0
    # The operator's ceiling.
    ceiling = limits.resolve(db.get(User, user_id), "documents")
    if ceiling is not None and total >= ceiling:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"you have {total} documents, the limit is {ceiling} — "
                   f"remove one first")

    document = UserDocument(user_id=user_id, kind=kind, filename=file.filename or "upload",
                            text=extracted.text, text_sha256=extracted.sha256,
                            chars=extracted.chars)
    db.add(document)
    db.flush()
    _documents_changed(db, user_id)
    audit(logger, "document.uploaded", user=user_id, kind=kind, chars=extracted.chars,
          truncated=extracted.truncated)
    return _stored("stored", document.id, extracted)


@router.get("")
def list_documents(db: Session = Depends(get_db),
                   user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """What the user has uploaded."""
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
