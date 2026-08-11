"""What a button tap means, in rows."""
from __future__ import annotations

import logging
import uuid as _uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from libs.db.models import User, UserJobState, UserLabel
from libs.obs import audit

logger = logging.getLogger(__name__)

# 5x an LLM label.
FEEDBACK_WEIGHT = 5.0

# What each action asserts about the job, on the same 0-100 scale the rubric uses.
APPLIED_LABEL = 90
SKIPPED_LABEL = 10

ACTIONS = ("applied", "skipped", "dismissed")


@dataclass(frozen=True)
class FeedbackResult:
    action: str
    job_id: int
    # Whether this changed anything.
    changed: bool


def record(session: Session, user_id: _uuid.UUID, job_id: int, action: str, *,
           create_missing: bool = False) -> FeedbackResult:
    """Record one action."""
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}")

    user = session.get(User, user_id)
    if user is None:
        raise ValueError(f"no such user: {user_id}")

    state = session.get(UserJobState, (user_id, job_id))
    if state is None and create_missing:
        state = UserJobState(user_id=user_id, job_id=job_id)
        session.add(state)
        session.flush()
    if state is None:
        # A tap on a job this user was never shown.
        logger.warning("feedback: user %s has no state for job %s", user_id, job_id)
        return FeedbackResult(action, job_id, changed=False)

    changed = _apply(state, action)
    if changed and action in ("applied", "skipped"):
        label = APPLIED_LABEL if action == "applied" else SKIPPED_LABEL
        # Upserted: what the user did replaces what the model guessed.
        session.execute(
            pg_insert(UserLabel)
            .values(user_id=user_id, job_id=job_id, label=label, source="feedback",
                    weight=FEEDBACK_WEIGHT, profile_version=user.profile_version,
                    profile_generation=user.profile_generation)
            .on_conflict_do_update(
                index_elements=["user_id", "job_id"],
                set_={"label": label, "source": "feedback", "weight": FEEDBACK_WEIGHT,
                      "profile_version": user.profile_version,
                      "profile_generation": user.profile_generation}))

    # Any action is engagement, and engagement is what the accounts list reports.
    user.last_active_at = func.now()
    session.flush()

    if changed:
        # Only counted when it is about to be written down.
        count = session.scalar(
            select(func.count()).select_from(UserLabel)
            .where(UserLabel.user_id == user_id, UserLabel.source == "feedback",
                   UserLabel.profile_generation == user.profile_generation)) or 0
        audit(logger, f"feedback.{action}", user=user_id, job=job_id, feedback_labels=count)
    return FeedbackResult(action, job_id, changed=changed)


def _apply(state: UserJobState, action: str) -> bool:
    """Set the timestamp for this action. Returns whether it was not already set."""
    if action == "applied":
        if state.applied_at is not None:
            # Never moved.
            return False
        state.applied_at = func.now()
        return True

    if action == "skipped":
        if state.dismissed_at is not None:
            return False
        state.dismissed_at = func.now()
        return True

    if state.dismissed_at is not None:
        return False
    state.dismissed_at = func.now()
    return True
