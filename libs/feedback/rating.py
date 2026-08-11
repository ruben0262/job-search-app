"""`/rate` — which job to ask the user about next."""
from __future__ import annotations

import uuid as _uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from libs.db.models import CorpusJob, User, UserJobState, UserLabel

# The score at which a job becomes worth sending.
DEFAULT_THRESHOLD = 60.0

# Below this, the row is a title and some boilerplate.
MIN_DESCRIPTION = 200


def rating_pool(session: Session, user_id: _uuid.UUID, *, threshold: float = DEFAULT_THRESHOLD,
                limit: int = 10) -> list[int]:
    """Job ids to ask this user about, closest to the boundary first."""
    user = session.get(User, user_id)
    if user is None:
        return []

    answered = select(UserLabel.job_id).where(
        UserLabel.user_id == user_id, UserLabel.source == "feedback")

    return list(session.scalars(
        select(UserJobState.job_id)
        .join(CorpusJob, CorpusJob.id == UserJobState.job_id)
        .where(UserJobState.user_id == user_id,
               UserJobState.score.is_not(None),
               # A score from an older model is not evidence about this model's boundary.
               UserJobState.scored_model_version == user.model_version,
               UserJobState.applied_at.is_(None),
               # One column covers "skipped" and "dismissed": both mean the user has answered about this job.
               UserJobState.dismissed_at.is_(None),
               UserJobState.job_id.notin_(answered),
               func.length(func.coalesce(CorpusJob.description, "")) >= MIN_DESCRIPTION)
        .order_by(func.abs(UserJobState.score - threshold))
        .limit(limit)).all())


def next_to_rate(session: Session, user_id: _uuid.UUID,
                 *, threshold: float = DEFAULT_THRESHOLD) -> int | None:
    """The single most informative job to ask about, or None when there is nothing left."""
    pool = rating_pool(session, user_id, threshold=threshold, limit=1)
    return pool[0] if pool else None
