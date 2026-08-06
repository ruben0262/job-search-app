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

from sqlalchemy import func, select, true
from sqlalchemy.orm import Session

from libs.db.models import CorpusJob, User, UserConfig, UserJobState
from libs.obs import Timings, audit, report_timings, timed
from libs.scoring import centroid
from libs.tenant.filters import UserFilters
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


def _in_countries(countries):
    """The SQL for "in one of these countries, or we could not tell".

    `None` means the account named no location, which admits everywhere — expressed as `true()`
    rather than as an empty condition so the caller reads the same either way.

    **The NULL half is the important half.** 16% of corpus rows carry no readable country, and they
    are kept: a filter that dropped them would delete the remote-first postings, which are the most
    portable jobs on the board (`libs.corpus.geo` rule 2).
    """
    if not countries:
        return true()
    return CorpusJob.country.is_(None) | CorpusJob.country.in_(countries)


def _candidates(session, user, user_id, filters, limit):
    """The rows worth considering for this account, newest first.

    Every condition here is an indexed comparison that Postgres answers without the row ever
    reaching Python — which is what makes a 30,000-row corpus affordable per account.
    """
    return session.execute(
        select(CorpusJob.id, CorpusJob.title, CorpusJob.company, CorpusJob.description,
               CorpusJob.embedding, CorpusJob.embedding_dim, CorpusJob.embedding_version)
        .outerjoin(UserJobState,
                   (UserJobState.job_id == CorpusJob.id)
                   & (UserJobState.user_id == user_id))
        .where(CorpusJob.embedding.is_not(None),
               (UserJobState.scored_model_version.is_(None))
               | (UserJobState.scored_model_version != user.model_version),
               # **Nothing to apply for.** Not a preference, so it is not in `UserFilters`.
               CorpusJob.closed_at.is_(None),
               # **Their own freshness window, at last.** The operator's per-source setting decides
               # how far back a *source is asked*; this is how old a posting may be and still be
               # worth this account's model. Falls back to `first_seen_at` for the 16% of rows that
               # carry no posted date, which is the same fallback the delivery filter uses.
               (CorpusJob.posted_at >= filters.fresh_since())
               | (CorpusJob.posted_at.is_(None)
                  & (CorpusJob.first_seen_at >= filters.fresh_since())),
               # **The country filter, finally running.** `libs.corpus.geo` had no production
               # consumer for two days after it was written (ROADMAP 6ak); this is it. NULL passes:
               # a posting that names no place is kept, or the most portable jobs on the board
               # would be the first to go.
               _in_countries(filters.countries))
        # **Newest first.** There was no ordering at all, so a 28,000-row backlog was served in
        # whatever order Postgres returned it and a posting found a minute ago queued behind jobs
        # from a month back. The product's whole claim is that a job reaches somebody while it is
        # still worth applying to (ROADMAP 6au).
        .order_by(CorpusJob.first_seen_at.desc())
        .limit(limit)).mappings().all()


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

    # **The split between filtering and scoring, which the lane timings alone cannot show.**
    #
    # `lane_timings` already reports how long a `score` job ran; what it cannot say is whether that
    # was the filter or the model. The whole argument for filtering is that it is orders of
    # magnitude cheaper than what it protects, and a claim like that has to be measurable or it is
    # just a claim (CLAUDE.md §4). One line per batch, with the counts beside the milliseconds.
    timings = Timings()

    model = ModelRegistry(session).get(user_id)
    filters = UserFilters.from_config(
        session.scalar(select(UserConfig.config).where(UserConfig.user_id == user_id)))

    with timed("select", timings):
        rows = _candidates(session, user, user_id, filters, limit)

    if not rows:
        return 0, 0


    tenant = TenantRepository(session)

    def skip(job_id: int) -> None:
        """Record a row as considered, with no score.

        `pending_feed` requires `score IS NOT NULL`, so a skipped row can never be delivered, and
        `scored_model_version` stops the lane picking it up again until the model changes — which
        is also when the centroid and the filters were last recomputed. Without this the saving is
        spent back re-asking the same question every cycle.
        """
        tenant.record_score(user_id, job_id, score=None, model_version=user.model_version)

    # **The user's own excluded words, in Python rather than SQL.** A `LIKE` per term per row would
    # be a sequential scan over the corpus; a substring check over the few hundred rows SQL already
    # narrowed to is nothing. Counted by term, because "dropped 400 for 'senior'" is a config
    # problem somebody can fix and "dropped 400" is not.
    excluded: dict[str, int] = {}
    kept = []
    with timed("exclude", timings):
        for row in rows:
            term = filters.excluded(row["title"], row["company"])
            if term is None:
                kept.append(row)
            else:
                excluded[term] = excluded.get(term, 0) + 1
                skip(row["id"])

    # **The centroid**, on whatever survived. It needs the embedding, so it cannot run in SQL.
    #
    # `keep` returns all-True when there is no centroid, which is the fail-open: an account in cold
    # start, or a model fitted before the column existed, has every row scored (ROADMAP 6an).
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

    # **The number the whole design rests on**, in one line: how long filtering took against how
    # long the model took, with what each stage removed.
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
