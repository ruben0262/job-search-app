"""`/rate` — which job to ask the user about next.

**Negatives cannot come from the feed.** A card scrolled past may have been missed rather than
rejected, and training on that teaches the model that inattention is dislike. The positives arrive
on their own: a build spends the user's own credit, so nobody presses it on a job they will not
apply to. What is left is a training set with one sign in it, and this is where the other comes
from — by asking.

## Ordered by distance from the threshold, not by score

The jobs where an answer teaches most are the ones the model is least sure about. A posting it
scored 96 and one it scored 4 are both already decided; a 58 and a 63 are where the boundary
actually is, and a label there moves it.

**Half the pool sits below the cutoff and was therefore never delivered.** That is the property
that makes this worth building. Rating only what reached the channel would teach the boundary from
one side — every "no" would come from jobs the model already ranked highly, and the model would
never hear that something it rejected was worth sending.

## What is excluded, and why

- **Jobs with no real description.** The user would be judging a job title, and so would the model
  they are teaching.
- **Jobs already acted on.** A second opinion on a job they applied to is not new information, and
  asking again reads as the platform not listening.
- **Jobs whose score is stale.** A score from a previous model says nothing about where the current
  model's boundary is, which is the thing being probed.
"""
from __future__ import annotations

import uuid as _uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from libs.db.models import CorpusJob, User, UserJobState, UserLabel

# The score at which a job becomes worth sending. The same figure the notifier delivers on, and
# what "the boundary" means everywhere in this module.
DEFAULT_THRESHOLD = 60.0

# Below this, the row is a title and some boilerplate. Rating it would produce a label about a
# posting nobody read.
MIN_DESCRIPTION = 200


def rating_pool(session: Session, user_id: _uuid.UUID, *, threshold: float = DEFAULT_THRESHOLD,
                limit: int = 10) -> list[int]:
    """Job ids to ask this user about, closest to the boundary first.

    Deliberately **not** restricted to what was delivered: about half of these are below the
    threshold and never reached the channel, which is the only way the model hears that something
    it rejected should not have been.
    """
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
               # One column covers both "skipped" and "dismissed": `_apply` writes `dismissed_at`
               # for either, because both mean the user has given an answer about this job.
               UserJobState.dismissed_at.is_(None),
               UserJobState.job_id.notin_(answered),
               CorpusJob.trimmed_at.is_(None),
               func.length(func.coalesce(CorpusJob.description, "")) >= MIN_DESCRIPTION)
        .order_by(func.abs(UserJobState.score - threshold))
        .limit(limit)).all())


def next_to_rate(session: Session, user_id: _uuid.UUID,
                 *, threshold: float = DEFAULT_THRESHOLD) -> int | None:
    """The single most informative job to ask about, or None when there is nothing left."""
    pool = rating_pool(session, user_id, threshold=threshold, limit=1)
    return pool[0] if pool else None
