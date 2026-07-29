"""What a button tap means, in rows.

**This is the half that makes the product improve.** Everything upstream ranks jobs by what Haiku
thought of them against a rubric; this records what the person actually did, which is the higher-
quality signal and the only one that reflects a decision rather than an opinion
(`ONBOARDING_AND_PROCESSING.md` §5).

`user_labels.source = 'feedback'` and `weight = 5` have been in the schema since Phase 2 with
nothing writing them. The 5 is carried from the reference instance, where it was arrived at by
measurement rather than chosen.

**In `libs` because two callers need identical behaviour**: the Telegram webhook, and the web
dashboard when it grows the same buttons. A second implementation would drift, and the drift would
be silent — two ways of recording "applied" that disagree about the label, and a training set that
quietly means two things.
"""
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

# 5x an LLM label. Carried from the reference instance rather than invented: what somebody does is
# worth more than what a model thought, but not so much more that a handful of taps overwhelms a
# few thousand rubric labels.
FEEDBACK_WEIGHT = 5.0

# What each action asserts about the job, on the same 0-100 scale the rubric uses.
#
# **Not 100 and 0.** An application is strong evidence of a good match and not proof of a perfect
# one; people apply to things they are unsure about. Saturating the scale would teach the model that
# every applied job is identical, which throws away the ordering that makes a feed useful.
APPLIED_LABEL = 90
SKIPPED_LABEL = 10

ACTIONS = ("applied", "skipped", "dismissed")


@dataclass(frozen=True)
class FeedbackResult:
    action: str
    job_id: int
    # Whether this changed anything. A second tap on the same button is a person checking it
    # registered, not a new opinion — and it must not enqueue a second retrain.
    changed: bool
    labels_since_train: int


def record(session: Session, user_id: _uuid.UUID, job_id: int, action: str, *,
           create_missing: bool = False) -> FeedbackResult:
    """Record one action. Idempotent per (user, job, action).

    **Does not commit** — the caller owns the transaction, so the label and whatever the caller does
    with the result land together.

    `create_missing` is for the one caller that legitimately acts on a job this user was never
    shown: `/cv <link>`, where they pasted the posting themselves. It is opt-in rather than the
    default because the usual cause of a missing state row is a tap on a months-old message whose
    row has been trimmed, and silently creating state for that would manufacture training data out
    of an accident.
    """
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
        # A tap on a job this user was never shown. Not an error worth raising at somebody — the
        # message may be old and the row trimmed — but never silent either.
        logger.warning("feedback: user %s has no state for job %s", user_id, job_id)
        return FeedbackResult(action, job_id, changed=False, labels_since_train=0)

    changed = _apply(state, action)
    if changed and action in ("applied", "skipped"):
        label = APPLIED_LABEL if action == "applied" else SKIPPED_LABEL
        # Upserted: an LLM label for this job almost certainly exists, and the whole point is that
        # what the user did replaces what the model guessed.
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

    # Any action is engagement, and engagement is what keeps an account out of `dormant` — a user
    # who reads their feed every morning may never open the web app again after onboarding.
    user.last_active_at = func.now()
    session.flush()

    count = session.scalar(
        select(func.count()).select_from(UserLabel)
        .where(UserLabel.user_id == user_id, UserLabel.source == "feedback",
               UserLabel.profile_generation == user.profile_generation)) or 0

    if changed:
        audit(logger, f"feedback.{action}", user=user_id, job=job_id, feedback_labels=count)
    return FeedbackResult(action, job_id, changed=changed, labels_since_train=count)


def _apply(state: UserJobState, action: str) -> bool:
    """Set the timestamp for this action. Returns whether it was not already set."""
    if action == "applied":
        if state.applied_at is not None:
            # **Never moved.** `applied_at` is the date the person actually applied, and a later tap
            # must not rewrite history — the reference instance sets it on the first build and
            # leaves it, and the applied-log is the durable record of a real event.
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
