"""Scoring one user's unscored corpus rows, as queue work.

**The gap this closes.** The harvest cycle stores and embeds; the notify worker delivers what is
already scored. Until this existed, *nothing* turned a corpus row into a score, so every account's
feed was frozen at whatever the last manual run produced — and worse, invisibly: once the scored
backlog is delivered, `pending_matches` falls to zero and the silent-feed alert goes quiet, because
from outside, a feed with nothing to send looks exactly like a feed with nothing new.

**Why a lane rather than a call from the notifier.** `services.notifier` may not import
`services.scorer` — services talk over the queue, never by import (`CODE_STRUCTURE.md` §2) — and
that contract is load-bearing here rather than ceremonial: the scorer's fail-closed version checks
belong in the one deployable that owns them, not spread across whichever service happened to need a
score. A lane also gets round-robin fairness for free, so one account with a large backlog cannot
starve everyone else's.

**`scored_model_version` is what makes this cheap.** A row already scored by the current model is
skipped; a retrain makes everything stale at once, which is correct — the scores mean something
different afterwards.
"""
from __future__ import annotations

import logging
import uuid as _uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from libs.db.models import CorpusJob, User, UserJobState
from libs.obs import audit
from libs.tenant.repository import TenantRepository
from services.scorer.registry import ModelRegistry, ModelUnavailable
from services.scorer.serve import score_rows

logger = logging.getLogger(__name__)

# Rows per database round trip. 500 is the same figure the migration script uses; scoring is a numpy
# operation over an already-loaded model, so the batch exists to bound memory and transaction size
# rather than to amortise anything expensive.
BATCH = 500

# **One queue job is one batch.** The worker commits the handler's effect and the job's completion
# together and forbids a handler from committing (`libs/queue/worker.py`), so the queue job's size
# is the unit of work a crash destroys. Unlike labelling this costs no money — but a 4,000-row
# transaction held open across a model load is a long lock for no benefit, and re-enqueueing while
# rows remain is what lets two accounts interleave.
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


def score_batch(session: Session, user_id: _uuid.UUID, *, limit: int = MAX_PER_RUN) -> tuple[int, int]:
    """Score up to `limit` rows. Returns (scored, remaining).

    **Does not commit** — the worker owns the transaction, so the scores and the "job done" record
    land together or neither does.

    A `ModelUnavailable` is left to the caller: it is the difference between "this account has no
    model yet", which is normal for a new signup and must not dead-letter, and a genuine version
    mismatch, which must not be scored through.
    """
    user = session.get(User, user_id)
    if user is None:
        return 0, 0

    model = ModelRegistry(session).get(user_id)
    rows = session.execute(
        select(CorpusJob.id, CorpusJob.title, CorpusJob.description,
               CorpusJob.embedding, CorpusJob.embedding_dim, CorpusJob.embedding_version)
        .outerjoin(UserJobState,
                   (UserJobState.job_id == CorpusJob.id)
                   & (UserJobState.user_id == user_id))
        .where(CorpusJob.embedding.is_not(None),
               (UserJobState.scored_model_version.is_(None))
               | (UserJobState.scored_model_version != user.model_version))
        .limit(limit)).mappings().all()

    if not rows:
        return 0, 0

    tenant = TenantRepository(session)
    bands = {"high": 0, "needs-review": 0, "skip": 0}
    for result in score_rows(model, [dict(row) for row in rows]):
        tenant.record_score(user_id, result.job_id, score=result.score,
                            model_version=user.model_version)
        bands[result.decision] = bands.get(result.decision, 0) + 1
    session.flush()

    remaining = unscored_count(session, user_id)
    audit(logger, "score.batch", user=user_id, scored=len(rows), remaining=remaining,
          model_version=user.model_version, **bands)
    return len(rows), remaining


__all__ = ["BATCH", "MAX_PER_RUN", "ModelUnavailable", "score_batch", "unscored_count"]
