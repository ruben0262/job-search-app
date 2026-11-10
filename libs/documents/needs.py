"""What a person must add before a CV or cover letter can be made for them (owner, 2026-11-10).

The Make buttons show for everyone. Pressed without these, the answer is what is missing and where to
add it: their AI account in use, and their own CV for a CV, their own cover letter for a cover letter.
Asked by the API before a build is queued and by the worker before any text leaves the server, so the
two cannot disagree.
"""
from __future__ import annotations

import uuid as _uuid

from pymongo.database import Database

from libs.db.models import UserDocument

# The upload each one is written from.
BASE = {"cv": "cv", "cover": "cover_letter"}


def missing(db: Database, user_id: _uuid.UUID, kind: str, *, account: bool) -> dict | None:
    """What is missing before a `kind` ("cv" or "cover") can be made, as the page shows it:
    `{"missing": [...], "message": ...}`, where "account" is no AI account in use and "cv" or
    "cover_letter" is none uploaded and switched on. None when nothing is."""
    base = BASE[kind]
    rows = db[UserDocument.COLLECTION].find({"user_id": user_id, "kind": base}, {"in_use": 1})
    uploaded = [row.get("in_use") is not False for row in rows]  # a row from before the switch is on
    lacking = [need for need, absent in (("account", not account), (base, not any(uploaded))) if absent]
    if not lacking:
        return None
    steps = {"account": "link your AI account in Settings",
             "cv": "switch your CV on in My documents" if uploaded else "upload your CV in My documents",
             "cover_letter": "switch a cover letter on in My documents" if uploaded
             else "upload a cover letter in My documents"}
    name = "CV" if kind == "cv" else "cover letter"
    return {"missing": lacking,
            "message": f"To make your {name}, first {' and '.join(steps[each] for each in lacking)}."}
