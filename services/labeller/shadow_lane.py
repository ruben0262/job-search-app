"""Asking the judge about a sample of what the model already scored.

**The one check that can notice a model going wrong.** A trained model serves for months without an
error or a warning when its numbers stop meaning what they meant. Sending 10% of what it scores
back through the judge and keeping both opinions side by side is what makes that visible
(`libs/scoring/shadow.py` for why the sampling is deterministic).

Two outputs, and the second is the one that compounds:

1. **A pair** — the model's score and the judge's, for drift measurement.
2. **A label** — because the judge's opinion *is* the training signal on this platform, so a
   shadow run is also a free label at the current generation. Discarding it would mean paying for
   the same answer twice.

It runs on the user's own credential and counts against their daily quota, which is why the sample
is small and why nothing here re-asks about a pair already collected.
"""
from __future__ import annotations

import logging
import uuid as _uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from libs.db.models import CorpusJob, ShadowScore, User, UserJobState
from libs.llm.client import LlmClient, LlmError
from libs.obs import audit
from libs.scoring.shadow import DEFAULT_SAMPLE_RATE, agrees, is_sampled
from services.labeller.label import score_batch, write_labels

logger = logging.getLogger(__name__)

# Jobs per shadow run. Much smaller than a relabel batch: this is a periodic health check on a
# model that is working, not a backlog to clear, and every row is money.
MAX_PER_RUN = 25


def candidates(session: Session, user_id: _uuid.UUID, *, limit: int = MAX_PER_RUN,
               rate: int = DEFAULT_SAMPLE_RATE) -> list[int]:
    """Job ids in this user's shadow sample that have not been compared yet.

    Sampled in Python rather than in SQL, because the sample must be identical to the one every
    other process would compute and a Postgres hash function is not the same function as
    `hashlib.sha256`. The candidate set is bounded first so this stays a scan of recent scores
    rather than of the user's whole history.
    """
    compared = select(ShadowScore.job_id).where(ShadowScore.user_id == user_id)
    rows = session.execute(
        select(UserJobState.job_id)
        .join(CorpusJob, CorpusJob.id == UserJobState.job_id)
        .where(UserJobState.user_id == user_id,
               UserJobState.score.is_not(None),
               UserJobState.job_id.notin_(compared),
               # No description, no comparison worth making: the judge would be grading a title
               # while the model scored an embedding of the same title, and they would agree for
               # reasons that say nothing about drift.
               func.length(func.coalesce(CorpusJob.description, "")) >= 200)
        .order_by(UserJobState.scored_at.desc())
        .limit(limit * (100 // max(rate, 1)) if rate else 0)).scalars().all()

    return [job_id for job_id in rows if is_sampled(user_id, job_id, rate=rate)][:limit]


def run_shadow(session: Session, user_id: _uuid.UUID, client: LlmClient, *,
               limit: int = MAX_PER_RUN, rate: int = DEFAULT_SAMPLE_RATE) -> tuple[int, int]:
    """Compare the judge against the model on one sample. Returns (compared, disagreements).

    **Does not commit** — the worker owns the transaction, as everywhere else in this service.
    """
    user = session.get(User, user_id)
    if user is None or not user.rubric_md:
        raise LlmError("this account has no rubric — confirm a profile first")

    job_ids = candidates(session, user_id, limit=limit, rate=rate)
    if not job_ids:
        return 0, 0

    jobs = list(session.scalars(select(CorpusJob).where(CorpusJob.id.in_(job_ids))).all())
    scored = score_batch(client, user.rubric_md, jobs)

    model_scores = dict(session.execute(
        select(UserJobState.job_id, UserJobState.score)
        .where(UserJobState.user_id == user_id, UserJobState.job_id.in_(job_ids))).all())

    by_url = {job.url: job for job in jobs}
    compared = disagreements = 0
    for url, judged in scored.items():
        job = by_url.get(url)
        if job is None:
            continue
        judge_score = float(judged["score"])
        model_score = model_scores.get(job.id)
        agreement = agrees(model_score, judge_score)
        session.execute(
            pg_insert(ShadowScore)
            .values(user_id=user_id, job_id=job.id,
                    model_score=model_score, judge_score=judge_score, agreed=agreement,
                    rationale=(judged.get("fit_notes") or "").strip()[:500] or None,
                    model_version=user.model_version,
                    profile_generation=user.profile_generation)
            .on_conflict_do_nothing(index_elements=["user_id", "job_id"]))
        compared += 1
        disagreements += agreement is False

    # The judge's answer is the training signal, so a shadow run is also a label. Paying for the
    # same opinion twice would be the alternative.
    write_labels(session, user_id, scored, jobs)
    session.flush()

    audit(logger, "shadow.batch", user=user_id, compared=compared,
          disagreements=disagreements, rate=rate, model_version=user.model_version)
    return compared, disagreements


def agreement_rate(session: Session, user_id: _uuid.UUID, *, model_version: int | None = None,
                   limit: int = 200) -> tuple[int, float | None]:
    """How often the judge and the model have agreed lately. Returns (pairs, rate).

    Scoped to a model version by default at the call site, because a pair collected before a
    retrain says nothing about the model serving now — averaging across versions would smear a
    fixed problem over the fix.
    """
    query = (select(ShadowScore.agreed)
             .where(ShadowScore.user_id == user_id, ShadowScore.agreed.is_not(None))
             .order_by(ShadowScore.created_at.desc())
             .limit(limit))
    if model_version is not None:
        query = query.where(ShadowScore.model_version == model_version)

    pairs = list(session.scalars(query).all())
    if not pairs:
        return 0, None
    return len(pairs), sum(pairs) / len(pairs)
