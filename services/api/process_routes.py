"""Process data: what it will cost, starting it, and confirming what it produced."""
from __future__ import annotations

import logging
import uuid as _uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from libs.contracts.profile import ProfileSchema
from libs.contracts.profile import json_schema as profile_schema
from libs.db.models import QueueJob, User, UserDocument, UserLabel
from libs.labels import POOL_SIZE, labelling_state
from libs.obs import audit
from libs.onboarding import CLAUDE_KINDS
from libs.profiles.repository import ProfileFiles
from libs.queue import enqueue
from libs.tenant.repository import TenantRepository
from services.api.account_routes import has_consented
from services.api.deps import current_user, get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/process", tags=["process"])

# Per-job relabelling cost in cents, for the warning text.
RELABEL_CENTS_PER_JOB = 0.18


@router.get("/state")
def state(db: Session = Depends(get_db), user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """What the account needs next, and what it will cost."""
    user = db.get(User, user_id)
    documents = db.scalar(select(func.count()).select_from(UserDocument)
                          .where(UserDocument.user_id == user_id)) or 0
    labels = db.scalar(select(func.count()).select_from(UserLabel)
                       .where(UserLabel.user_id == user_id,
                              UserLabel.profile_generation == user.profile_generation)) or 0

    # A profile the setup agents finished is built, though they never touch `processed_at`.
    setup_done = not ProfileFiles(db, user_id).missing_setup()
    never_processed = user.processed_at is None and not setup_done
    documents_changed = bool(
        user.documents_changed_at and user.processed_at
        and user.documents_changed_at > user.processed_at)

    if user.profile_draft:
        # Ahead of everything else: a draft sitting unreviewed is the state the user is in.
        action, message = "confirm", "Your profile is ready to review."
    elif never_processed and documents:
        action, message = "process", "Process your documents to build your profile."
    elif documents_changed:
        action = "process"
        message = "You have added documents since your profile was built. Process them to update it."
    elif setup_done and not user.profile:
        # Built by the conversation: nothing to process, and what it needs next is labels.
        action, message = "ready", "Your profile is ready. Scoring can be trained from here."
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
        # The warning that names consequences instead of asking "are you sure?".
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
    """Queue a processing run."""
    if not any(kind in _stored_kinds(db, user_id) for kind in CLAUDE_KINDS):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="processing runs on your own Claude credential — add one first")
    if not db.scalar(select(UserDocument.id).where(UserDocument.user_id == user_id).limit(1)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="upload at least your CV before processing")
    if not has_consented(db, user_id):
        # The only place document text crosses a processor boundary, so the only place consent is checked.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="processing sends your document text to Anthropic — please agree first")

    # One pending run per user, collapsed by the queue's partial unique index, not by an app check.
    job_id = enqueue(db, "process", user_id, dedup_key="process")
    audit(logger, "process.requested", user=user_id, job=job_id)
    return {"status": "queued" if job_id else "already queued", "job_id": job_id}


@router.get("/draft")
def draft(db: Session = Depends(get_db), user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """The proposal, plus the schema the confirmation form is generated from."""
    user = db.get(User, user_id)
    if not user.profile_draft:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="nothing to review")
    return {"draft": user.profile_draft, "schema": profile_schema(),
            "source": user.capture_source}


@router.post("/confirm")
def confirm(body: ProfileSchema, db: Session = Depends(get_db),
            user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Apply the profile the user has read and edited."""
    user = db.get(User, user_id)
    if not user.profile_draft:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail="there is no draft to confirm — run processing first")

    profile = body.model_dump()
    profile["confirmed"] = True
    generation = TenantRepository(db).save_profile(user_id, profile)
    user.profile_draft = None

    # Queued here, not left to the dashboard.
    enqueue(db, "label", user_id, dedup_key="label")

    audit(logger, "process.confirmed", user=user_id, generation=generation,
          not_list=len(body.not_list))
    return {
        "status": "confirmed",
        "profile_generation": generation,
        # Says what just became stale rather than leaving them to discover it.
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

# The lanes that make up "training" from a user's point of view.
TRAINING_LANES = ("label", "train")

# How long after a finished batch the run still counts as in progress.
BETWEEN_BATCHES = timedelta(minutes=10)


@router.get("/training")
def training_state(db: Session = Depends(get_db),
                   user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """What training is doing right now, for a page that polls."""
    rows = db.execute(
        select(QueueJob.lane, QueueJob.state, func.count(), func.max(QueueJob.last_error))
        .where(QueueJob.user_id == user_id, QueueJob.lane.in_(TRAINING_LANES))
        .group_by(QueueJob.lane, QueueJob.state)).all()

    lanes: dict[str, dict] = {}
    for lane, state_name, count, last_error in rows:
        entry = lanes.setdefault(lane, {"ready": 0, "claimed": 0, "done": 0, "failed": 0,
                                        "last_error": None})
        entry[state_name] = entry.get(state_name, 0) + count
        if state_name == "failed" and last_error:
            entry["last_error"] = last_error[:400]

    # "Running" is not "a row is queued this instant".
    in_queue = any(lane.get("ready", 0) or lane.get("claimed", 0) for lane in lanes.values())
    latest = db.scalar(
        select(func.max(QueueJob.finished_at))
        .where(QueueJob.user_id == user_id, QueueJob.lane.in_(TRAINING_LANES)))
    recently_worked = bool(
        latest and (datetime.now(UTC) - latest) < BETWEEN_BATCHES)
    user_paused = bool(getattr(db.get(User, user_id), "labelling_paused", False))
    labelling = labelling_state(db, user_id)
    # `train` is a readiness state, not work in progress: an account with enough labels sits in it for ever.
    unfinished = labelling["action"] in ("cold_start", "relabel")
    running = in_queue or (recently_worked and unfinished)
    # A paused account is not running, whatever is still draining.
    if user_paused:
        running = False
    user = db.get(User, user_id)

    return {
        # Whether anything is in flight, which is what decides between a progress line and a button.
        "running": running,
        "paused": user_paused,
        "phase": labelling["action"],
        "labels": labelling["labels"],
        "target": POOL_SIZE,
        "stale_labels": labelling["stale"],
        "lanes": lanes,
        "model_version": user.model_version if user else None,
        # Surfaced rather than buried in a log: a failed lane is the one thing a user can act on.
        "failures": {lane: data["last_error"] for lane, data in lanes.items()
                     if data.get("failed") and data.get("last_error")},
    }


@router.post("/training/stop")
def stop_training(db: Session = Depends(get_db),
                  user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Cancel queued training work for this account."""
    # The flag first.
    user = db.get(User, user_id)
    if user is not None:
        user.labelling_paused = True
        db.flush()

    cancelled = db.execute(
        delete(QueueJob)
        .where(QueueJob.user_id == user_id,
               QueueJob.lane.in_(TRAINING_LANES),
               QueueJob.state == "ready")
        .returning(QueueJob.id)).all()
    audit(logger, "training.stopped", user=user_id, cancelled=len(cancelled))
    return {"stopped": True, "cancelled": len(cancelled),
            "detail": ("Nothing new will be picked up. A batch already running will finish, and "
                       "every label written so far is kept.")}

