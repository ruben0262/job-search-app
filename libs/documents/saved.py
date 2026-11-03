"""The CVs and cover letters a person keeps: within their storage allowance, until they delete them.

One answer to "is there room", read by the docgen worker that keeps a document and by the page that
shows how full the storage is.
"""
from __future__ import annotations

import logging
import uuid as _uuid
from datetime import timedelta

from pymongo.database import Database

from libs import limits, notify
from libs.db import get, insert, now
from libs.db.models import BuiltDocument, User

logger = logging.getLogger(__name__)


def allowance_bytes(user: User) -> int | None:
    """How much this person may keep; None is unlimited."""
    megabytes = limits.resolve(user, "document_storage_mb")
    return None if megabytes is None else megabytes * 1024 * 1024


def used_bytes(db: Database, user_id: _uuid.UUID) -> int:
    """What their kept documents take up."""
    total = list(db[BuiltDocument.COLLECTION].aggregate([
        {"$match": {"user_id": user_id, "saved": True}},
        {"$group": {"_id": None, "bytes": {"$sum": "$size"}}}]))
    return total[0]["bytes"] if total else 0


def keep(db: Database, *, build_id: int, user_id: _uuid.UUID, job_id: int, kind: str, mode: str,
         filename: str, pdf: bytes, ats_ok: bool | None = None) -> bool:
    """Store a finished document, kept if it fits, and say whether it was. A newer one for the same job
    and kind replaces the older, so the room it frees counts. When it does not fit, it waits for its
    download as every document used to, and the person is told their storage is full."""
    replaced = db[BuiltDocument.COLLECTION].find(
        {"user_id": user_id, "job_id": job_id, "kind": kind, "saved": True}, {"size": 1})
    freed = sum(row["size"] for row in replaced)
    allowance = allowance_bytes(get(db, User, user_id))
    saved = allowance is None or used_bytes(db, user_id) - freed + len(pdf) <= allowance
    if saved:
        db[BuiltDocument.COLLECTION].delete_many({"user_id": user_id, "job_id": job_id, "kind": kind})
    else:
        logger.info("documents: %s's storage is full; document %s was not kept", user_id, build_id)
        notify.notify(db, user_id, notify.STORAGE_FULL,
                      "New CVs and cover letters are not being kept. Delete some in My documents to "
                      "make room; until then each one can be downloaded for a short while after it is made.")
    insert(db, BuiltDocument(id=build_id, user_id=user_id, job_id=job_id, kind=kind, mode=mode,
                             filename=filename, content=pdf, size=len(pdf), saved=saved, ats_ok=ats_ok))
    return saved


def purge_unsaved(db: Database, hours: int) -> int:
    """D9 for what was not kept: deleted once it is `hours` old, downloaded or not."""
    return db[BuiltDocument.COLLECTION].delete_many(
        {"saved": False, "created_at": {"$lt": now() - timedelta(hours=hours)}}).deleted_count


def for_jobs(db: Database, user_id: _uuid.UUID, job_ids: list[int]) -> dict[int, list[dict]]:
    """The documents this person has for each of these jobs, newest first: what a job card offers to
    download again. Without the PDFs themselves."""
    found: dict[int, list[dict]] = {}
    for row in db[BuiltDocument.COLLECTION].find(
            {"user_id": user_id, "job_id": {"$in": job_ids}}, {"content": 0}, sort=[("created_at", -1)]):
        found.setdefault(row["job_id"], []).append(row)
    return found
