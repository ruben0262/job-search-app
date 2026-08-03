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

    # **A profile the setup agents finished is a built profile**, even though they never touch
    # `processed_at` -- that column belongs to the capture flow they replaced (`REBUILD_PLAN.md`).
    # Without this an account that has just completed setup was told to "process your documents to
    # build your profile", which would have run the superseded flow over a finished profile and
    # replaced it. The same predicate the trainer refuses on, so the two cannot disagree.
    setup_done = not ProfileFiles(db, user_id).missing_setup()
    never_processed = user.processed_at is None and not setup_done
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
    elif setup_done and not user.profile:
        # Built by the conversation rather than the capture flow: nothing to process, and the next
        # thing this account needs is labels and a model rather than another pass over documents.
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
    if not has_consented(db, user_id):
        # **The only place document text crosses a processor boundary**, so it is the only place
        # the consent is owed and the only place it is checked. Refused here rather than in the
        # worker: a job that dies on a consent check is a failure the user cannot see, hours later
        # (`ROADMAP.md` Phase 3 step 11).
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="processing sends your document text to Anthropic — please agree first")

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

    # **Queued here, not left to the dashboard.** Confirming has just made every existing label
    # answer a different question, so the account is at zero usable labels for the new generation
    # until this runs — and an account that cannot train is not a state to leave someone in because
    # they did not press a second button. The one-directional coupling of
    # `ONBOARDING_AND_PROCESSING.md` §4, enforced rather than documented.
    enqueue(db, "label", user_id, dedup_key="label")

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

# The lanes that make up "training" from a user's point of view. Labelling is most of the wall
# clock and the part that costs money; the fit at the end is seconds.
TRAINING_LANES = ("label", "train", "score")

# How long after a finished batch the run still counts as in progress. Comfortably longer than the
# dispatcher's cycle, so an ordinary gap between batches never reads as "stopped", and short enough
# that a genuinely abandoned run stops claiming to be alive.
BETWEEN_BATCHES = timedelta(minutes=10)


@router.get("/training")
def training_state(db: Session = Depends(get_db),
                   user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """What training is doing right now, for a page that polls.

    **The work does not live in the browser and never did.** Pressing the button enqueues rows in
    Postgres and the worker pools drain them, so closing the tab, switching pages or losing the
    laptop changes nothing about whether it finishes. This endpoint exists because that was
    invisible: the page said "Starting…" once and then looked identical whether the run was halfway
    through or had died an hour ago.

    Derived on read rather than tracked in a column, for the reason every other state on this
    platform is: a progress counter that can disagree with the queue eventually will.
    """
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

    # **"Running" is not "a row is queued this instant".**
    #
    # Labelling goes in batches: a worker takes one job, writes a batch, finishes it, and the
    # dispatcher enqueues the next. Between those two moments the queue for this account is empty,
    # and reading only `ready`/`claimed` made the page flip back to its idle button mid-run -- so a
    # user who navigated away and returned was told nothing was happening while it plainly was.
    #
    # So a recent finish counts too, for as long as the phase still has work left in it. That is
    # also what makes this survive a refresh: it is derived from the queue and the label counts,
    # neither of which cares whether anybody had the page open.
    in_queue = any(lane.get("ready", 0) or lane.get("claimed", 0) for lane in lanes.values())
    latest = db.scalar(
        select(func.max(QueueJob.finished_at))
        .where(QueueJob.user_id == user_id, QueueJob.lane.in_(TRAINING_LANES)))
    recently_worked = bool(
        latest and (datetime.now(UTC) - latest) < BETWEEN_BATCHES)
    user_paused = bool(getattr(db.get(User, user_id), "labelling_paused", False))
    labelling = labelling_state(db, user_id)
    unfinished = labelling["action"] in ("cold_start", "relabel", "train")
    running = in_queue or (recently_worked and unfinished)
    # A paused account is not running, whatever is still draining. Saying otherwise is what made
    # Stop look like it had done nothing.
    if user_paused:
        running = False
    user = db.get(User, user_id)

    return {
        # **Whether anything is in flight**, which is what decides between a progress line and a
        # button. Claimed counts as running: a worker holds it and will finish or time out.
        "running": running,
        "paused": user_paused,
        "phase": labelling["action"],
        "labels": labelling["labels"],
        "target": POOL_SIZE,
        "stale_labels": labelling["stale"],
        "lanes": lanes,
        "model_version": user.model_version if user else None,
        # Surfaced rather than buried in a log: a failed lane is the one thing a user can act on,
        # and "it stopped and nobody said why" is the state this endpoint exists to prevent.
        "failures": {lane: data["last_error"] for lane, data in lanes.items()
                     if data.get("failed") and data.get("last_error")},
    }


@router.post("/training/stop")
def stop_training(db: Session = Depends(get_db),
                  user_id: _uuid.UUID = Depends(current_user)) -> dict:
    """Cancel queued training work for this account.

    **Queued only.** A row a worker has already claimed is mid-call on the user's own credential;
    deleting it here would not stop that call, it would only lose the record of it — so the batch
    in flight finishes and nothing new is taken. That is also why the button warns rather than
    promises: stopping is prompt, not instant.

    Labels already written are kept. They were paid for, they are still true, and throwing them
    away would make stopping expensive enough that nobody would risk it.
    """
    # **The flag first.** Deleting queued rows alone never stopped anything: the labeller
    # re-enqueues itself while work remains, so a cancelled row was recreated a second later and
    # the run carried on. Set before the delete, so a worker finishing mid-request sees it.
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

