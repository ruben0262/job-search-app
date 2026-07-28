"""Process data: what it will cost, starting it, and confirming what it produced.

**State, not buttons** (`ONBOARDING_AND_PROCESSING.md` §4). These endpoints report which state the
account is in and what the next action costs; they do not offer four actions and hope they are
pressed in order. The one coupling that is enforced runs in a single direction — processing bumps
`profile_generation`, which makes every label stale — and the reverse is deliberately not enforced,
because forcing a re-process before a retrain would discard the very labels the retrain was for.

The confirmation step is the boundary this module exists to hold. An uploaded CV is untrusted text,
every `not_list` entry becomes a hard gate that silently suppresses jobs, and so nothing the model
wrote reaches a rubric until a human has read it and pressed a button (`PROFILE_CAPTURE_PLAN.md` §5).
"""
from __future__ import annotations

import logging
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from libs.contracts.profile import ProfileSchema
from libs.contracts.profile import json_schema as profile_schema
from libs.db.models import User, UserDocument, UserLabel
from libs.obs import audit
from libs.onboarding import CLAUDE_KINDS
from libs.queue import enqueue
from libs.tenant.repository import TenantRepository
from services.api.deps import current_user, get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/process", tags=["process"])

# Per-job relabelling cost in cents, for the warning text. **Measured, not guessed**: the reference
# instance labels at ~$0.09 per 50 jobs with Haiku (`COLD_START_PLAN.md` §4), i.e. $0.0018/job.
#
# Quoted without the Batch API's 50% discount on purpose — the estimate should be the one the user
# cannot be disappointed by. It is still called rough in the response, because a rubric twice as
# long costs more and this number came from one profile.
RELABEL_CENTS_PER_JOB = 0.18


@router.get("/state")
def state(db: Session = Depends(get_db), user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """What the account needs next, and what it will cost.

    Derived from the counters that already exist rather than from a status column: a column would
    need updating by whoever remembers, and the first thing to go stale would be the one that says
    whether anything is stale.
    """
    user = db.get(User, user_id)
    documents = db.scalar(select(func.count()).select_from(UserDocument)
                          .where(UserDocument.user_id == user_id)) or 0
    labels = db.scalar(select(func.count()).select_from(UserLabel)
                       .where(UserLabel.user_id == user_id,
                              UserLabel.profile_generation == user.profile_generation)) or 0

    never_processed = user.processed_at is None
    documents_changed = bool(
        user.documents_changed_at and user.processed_at
        and user.documents_changed_at > user.processed_at)

    if user.profile_draft:
        # Ahead of everything else: a draft sitting unreviewed is the state the user is actually in,
        # and telling them to process again would throw away the call they already paid for.
        action, message = "confirm", "Your profile is ready to review."
    elif never_processed and documents:
        action, message = "process", "Process your documents to build your profile."
    elif documents_changed:
        action = "process"
        message = "You have added documents since your profile was built. Process them to update it."
    elif not user.profile:
        action, message = "upload", "Upload your CV first — there is nothing to process yet."
    else:
        action, message = "ready", "Your profile is up to date."

    return {
        "action": action,
        "message": message,
        "documents": documents,
        "processed_at": user.processed_at.isoformat() if user.processed_at else None,
        "has_draft": bool(user.profile_draft),
        "profile_generation": user.profile_generation,
        # The warning that names consequences instead of asking "are you sure?". A generic
        # confirmation is one nobody reads; a number they recognise is one they think about.
        "warning": {
            "replaces_profile": bool(user.profile),
            "labels_invalidated": labels,
            "relabel_cost_estimate_usd": round(labels * RELABEL_CENTS_PER_JOB / 100, 2),
            "estimate_is_rough": True,
            "text": (f"This replaces your profile. Every job you have scored will need "
                     f"relabelling — about {labels} jobs, roughly "
                     f"${labels * RELABEL_CENTS_PER_JOB / 100:.2f} on your own key."),
        },
    }


@router.post("")
def start(db: Session = Depends(get_db), user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Queue a processing run. Idempotent while one is pending.

    Queued rather than run in the request: it is a single multi-minute Opus call over a whole
    document set, and holding an HTTP connection open for it would turn a slow model into a failed
    signup.
    """
    if not any(kind in _stored_kinds(db, user_id) for kind in CLAUDE_KINDS):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="processing runs on your own Claude credential — add one first")
    if not db.scalar(select(UserDocument.id).where(UserDocument.user_id == user_id).limit(1)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="upload at least your CV before processing")

    # One pending run per user, collapsed by the queue's partial unique index rather than by an
    # application check — a double-click must not buy two Opus calls.
    job_id = enqueue(db, "process", user_id, dedup_key="process")
    audit(logger, "process.requested", user=user_id, job=job_id)
    return {"status": "queued" if job_id else "already queued", "job_id": job_id}


@router.get("/draft")
def draft(db: Session = Depends(get_db), user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """The proposal, plus the schema the confirmation form is generated from.

    Returned whole — unlike `/documents`, which previews. The user is being asked to agree to every
    line of it, so showing them a summary would make the confirmation meaningless.
    """
    user = db.get(User, user_id)
    if not user.profile_draft:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="nothing to review")
    return {"draft": user.profile_draft, "schema": profile_schema(),
            "source": user.capture_source}


@router.post("/confirm")
def confirm(body: ProfileSchema, db: Session = Depends(get_db),
            user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Apply the profile the user has read and edited. **This is the only path to a live profile.**

    The body is the *edited* draft rather than a bare "yes", because the confirmation form is where
    a wrong `not_list` entry gets removed — and that is the entry that would otherwise silently
    suppress jobs the user wanted for as long as the profile lasts.

    `confirmed` is forced here rather than trusted from the request. A client that posted the raw
    draft back untouched would otherwise confirm on the user's behalf, which is the whole thing this
    step exists to prevent.
    """
    user = db.get(User, user_id)
    if not user.profile_draft:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail="there is no draft to confirm — run processing first")

    profile = body.model_dump()
    profile["confirmed"] = True
    generation = TenantRepository(db).save_profile(user_id, profile)
    user.profile_draft = None

    audit(logger, "process.confirmed", user=user_id, generation=generation,
          not_list=len(body.not_list))
    return {
        "status": "confirmed",
        "profile_generation": generation,
        # Says what just became stale rather than leaving them to discover it. Relabelling is the
        # next action and it is the expensive one.
        "next_action": "relabel",
    }


@router.delete("/draft")
def discard(db: Session = Depends(get_db), user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Throw the proposal away. The live profile, if there is one, is untouched."""
    user = db.get(User, user_id)
    user.profile_draft = None
    return {"status": "discarded"}


def _stored_kinds(db: Session, user_id: _uuid.UUID) -> set[str]:
    from libs.db.models import UserSecret  # noqa: PLC0415 - kept local to the one caller

    return set(db.scalars(select(UserSecret.kind).where(UserSecret.user_id == user_id)).all())
