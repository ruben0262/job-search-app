"""Scoring one user's unscored corpus rows, as queue work."""
from __future__ import annotations

import logging
import uuid as _uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from libs.db.models import CorpusJob, User, UserJobState
from libs.obs import Timings, audit, report_timings, timed
from libs.scoring import centroid
from libs.tenant.filters import candidate_conditions, filters_for
from libs.tenant.repository import TenantRepository
from services.scorer.registry import ModelRegistry, ModelUnavailable
from services.scorer.serve import score_rows

logger = logging.getLogger(__name__)

# Rows per database round trip.
BATCH = 500

# One queue job is one batch.
MAX_PER_RUN = BATCH


def unscored_count(session: Session, user_id: _uuid.UUID) -> int:
    """How many embedded rows this user's current model has not seen."""
    user = session.get(User, user_id)
    if user is None:
        return 0
    return session.scalar(
        select(func.count()).select_from(CorpusJob)
        .outerjoin(UserJobState,
                   (UserJobState.job_id == CorpusJob.id)
                   & (UserJobState.user_id == user_id))
        .where(CorpusJob.embedding.is_not(None),
               (UserJobState.scored_model_version.is_(None))
               | (UserJobState.scored_model_version != user.model_version))) or 0


def _candidates(session, user, user_id, filters, limit):
    """The rows worth considering for this account, newest first."""
    return session.execute(
        select(CorpusJob.id, CorpusJob.title, CorpusJob.company, CorpusJob.description,
               CorpusJob.embedding, CorpusJob.embedding_dim, CorpusJob.embedding_version,
               # What the delivery filter used to need at send time.
               CorpusJob.source, CorpusJob.applicants, CorpusJob.location,
               CorpusJob.is_remote, CorpusJob.workplace)
        .outerjoin(UserJobState,
                   (UserJobState.job_id == CorpusJob.id)
                   & (UserJobState.user_id == user_id))
        .where(CorpusJob.embedding.is_not(None),
               (UserJobState.scored_model_version.is_(None))
               | (UserJobState.scored_model_version != user.model_version),
               *candidate_conditions(filters))
        # Newest first.
        .order_by(CorpusJob.first_seen_at.desc())
        .limit(limit)).mappings().all()


def score_batch(session: Session, user_id: _uuid.UUID, *, limit: int = MAX_PER_RUN) -> tuple[int, int]:
    """Score up to `limit` rows."""
    user = session.get(User, user_id)
    if user is None:
        return 0, 0

    # The split between filtering and scoring, which the lane timings alone cannot show.
    timings = Timings()

    model = ModelRegistry(session).get(user_id)
    # The operator's ceiling and window for this account, resolved in one place (`libs.tenant.filters`).
    filters = filters_for(session, user)

    with timed("select", timings):
        rows = _candidates(session, user, user_id, filters, limit)

    if not rows:
        return 0, 0


    tenant = TenantRepository(session)

    def skip(job_id: int) -> None:
        """Record a row as considered, with no score."""
        tenant.record_score(user_id, job_id, score=None, model_version=user.model_version)

    # Everything the account could never receive, in Python rather than SQL.
    excluded: dict[str, int] = {}
    kept = []
    with timed("exclude", timings):
        for row in rows:
            reason = filters.unwanted(row)
            if reason is None:
                kept.append(row)
            else:
                excluded[reason] = excluded.get(reason, 0) + 1
                skip(row["id"])

    # The centroid, on whatever survived.
    with timed("centroid", timings):
        verdicts = centroid.keep([row["embedding"] for row in kept],
                                 centroid.unpack(model.centroid) if model.centroid else [],
                                 model.centroid_threshold or 0.0)
    worth_scoring = [row for row, ok in zip(kept, verdicts, strict=True) if ok]
    for row, ok in zip(kept, verdicts, strict=True):
        if not ok:
            skip(row["id"])

    off_centre = len(kept) - len(worth_scoring)
    if not worth_scoring:
        session.flush()
        remaining = unscored_count(session, user_id)
        audit(logger, "score.batch", user=user_id, scored=0, off_centre=off_centre,
              excluded=sum(excluded.values()), remaining=remaining,
              model_version=user.model_version)
        return 0, remaining

    bands = {"high": 0, "needs-review": 0, "skip": 0}
    with timed("score", timings):
        for result in score_rows(model, [dict(row) for row in worth_scoring]):
            tenant.record_score(user_id, result.job_id, score=result.score,
                                model_version=user.model_version)
            bands[result.decision] = bands.get(result.decision, 0) + 1
    session.flush()

    # The number the design rests on: filter time against model time, and what each stage removed.
    report_timings(timings, event="score.timing", log=logger, user=str(user_id),
                   candidates=len(rows), excluded=sum(excluded.values()),
                   off_centre=off_centre, scored=len(worth_scoring))

    remaining = unscored_count(session, user_id)
    if excluded:
        # By term rather than as a total, so the reader can see which word is emptying a feed.
        logger.info("score: %s excluded %s before scoring", user_id, excluded)
    audit(logger, "score.batch", user=user_id, scored=len(worth_scoring), off_centre=off_centre,
          excluded=sum(excluded.values()), remaining=remaining,
          model_version=user.model_version, **bands)
    return len(worth_scoring), remaining


__all__ = ["BATCH", "MAX_PER_RUN", "ModelUnavailable", "score_batch", "unscored_count"]
