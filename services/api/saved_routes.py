"""The CVs and cover letters a person has kept: listed with the room they take, deleted one by one or
all at once. Made by the docgen worker (`libs.documents.saved.keep`); downloaded from
`/documents/build/{id}/file`, like a document just made."""
from __future__ import annotations

import logging
import uuid as _uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from pymongo.database import Database

from libs.db import get
from libs.db.models import BuiltDocument, CorpusJob, User
from libs.documents.saved import allowance_bytes, used_bytes
from libs.obs import audit
from services.api.deps import current_user, get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/saved", tags=["documents"])


class JobDocument(BaseModel):
    """One document for one job, as a job card offers it."""
    id: int
    kind: Literal["cv", "cover"]
    mode: Literal["instant", "ai"]
    # False when it did not fit and is only waiting for its download.
    saved: bool
    download_url: str
    created_at: datetime


class SavedDocument(JobDocument):
    job_id: int
    job_title: str | None
    company: str | None
    size: int


class SavedDocuments(BaseModel):
    used_bytes: int
    # None is unlimited.
    allowance_bytes: int | None
    documents: list[SavedDocument]


def job_document(row: dict) -> JobDocument:
    return JobDocument(id=row["_id"], kind=row["kind"], mode=row["mode"], saved=row["saved"],
                       download_url=f"/documents/build/{row['_id']}/file", created_at=row["created_at"])


@router.get("")
def saved(db: Database = Depends(get_db), user_id: _uuid.UUID = Depends(current_user)) -> SavedDocuments:
    """Every kept document, newest first, and how full the storage is."""
    rows = list(db[BuiltDocument.COLLECTION].find({"user_id": user_id, "saved": True}, {"content": 0},
                                                  sort=[("created_at", -1)]))
    jobs = {doc["_id"]: doc for doc in db[CorpusJob.COLLECTION].find(
        {"_id": {"$in": [row["job_id"] for row in rows]}}, {"title": 1, "company": 1})}
    return SavedDocuments(
        used_bytes=used_bytes(db, user_id), allowance_bytes=allowance_bytes(get(db, User, user_id)),
        documents=[SavedDocument(**job_document(row).model_dump(), job_id=row["job_id"], size=row["size"],
                                 job_title=jobs.get(row["job_id"], {}).get("title"),
                                 company=jobs.get(row["job_id"], {}).get("company"))
                   for row in rows])


@router.delete("/{document_id}")
def delete_one(document_id: int, db: Database = Depends(get_db),
               user_id: _uuid.UUID = Depends(current_user)) -> SavedDocuments:
    """Delete one kept document."""
    gone = db[BuiltDocument.COLLECTION].delete_one({"_id": document_id, "user_id": user_id}).deleted_count
    if not gone:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such document")
    audit(logger, "documents.deleted", user=user_id, document=document_id)
    return saved(db, user_id)


@router.delete("")
def delete_all(db: Database = Depends(get_db), user_id: _uuid.UUID = Depends(current_user)) -> SavedDocuments:
    """Delete every kept document, to make room."""
    gone = db[BuiltDocument.COLLECTION].delete_many({"user_id": user_id}).deleted_count
    audit(logger, "documents.cleared", user=user_id, count=gone)
    return saved(db, user_id)
