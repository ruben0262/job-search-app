"""The `score` lane: the step that had been missing entirely.

Nothing turned a harvested row into a score until this existed, and the failure was invisible —
once the already-scored backlog is delivered, a feed with nothing to send looks exactly like a feed
with nothing new. So the tests are about the properties that keep it running unattended:

- an account with no model yet does **not** dead-letter, because that is the normal state of every
  new signup and the harvest cycle enqueues for everybody;
- a row already scored by the current model is not scored again, and a retrain makes everything
  stale at once;
- the continuation actually continues, rather than stopping after one batch and looking like
  success.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("numpy")

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (no live Postgres)")


@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from libs.db.models import CorpusJob, User

    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")

    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active",
                active=True, profile_generation=1, model_version=0)
    session.add(user)
    session.flush()

    jobs = [CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test",
                      title=f"Backend Engineer {n}", description="we build things",
                      embedding=b"\x00" * 8, embedding_dim=2, embedding_version="v1",
                      text_sha256=uuid.uuid4().hex * 2)
            for n in range(5)]
    session.add_all(jobs)
    session.flush()

    try:
        yield session, user, jobs
    finally:
        session.close()
        trans.rollback()
        conn.close()


def test_an_account_with_no_model_is_not_a_failure(db):
    """**The property that keeps the lane quiet.** The harvest cycle enqueues for every active
    account, and a new signup has no artifact until its first train. Dead-lettering those would
    fill the queue with red for the one state every account passes through."""
    from services.scorer.score_lane import ModelUnavailable, score_batch

    session, user, _ = db
    with pytest.raises(ModelUnavailable):
        score_batch(session, user.id)


def test_the_worker_swallows_a_missing_model_rather_than_dead_lettering(db):
    """The handler turns that exception into a no-op job, which is what stops a new account's queue
    from filling with failures before it has ever trained."""
    from libs.db.models import QueueJob
    from services.scorer.__main__ import handle

    session, user, _ = db
    job = QueueJob(lane="score", user_id=user.id, payload={}, dedup_key="score")
    session.add(job)
    session.flush()

    handle(session, job)  # must not raise


def test_unscored_count_ignores_rows_with_no_embedding(db):
    """A row without an embedding cannot be scored, so counting it would make `remaining` never
    reach zero and the continuation chain never stop."""
    from libs.db.models import CorpusJob
    from services.scorer.score_lane import unscored_count

    session, user, _ = db
    before = unscored_count(session, user.id)

    session.add(CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test",
                          title="Not embedded yet"))
    session.flush()

    assert unscored_count(session, user.id) == before


def test_a_row_scored_by_the_current_model_is_not_scored_again(db):
    """`scored_model_version` is what makes the lane cheap to re-run and safe to interrupt."""
    from libs.db.models import UserJobState
    from services.scorer.score_lane import unscored_count

    session, user, jobs = db
    user.model_version = 3
    session.add(UserJobState(user_id=user.id, job_id=jobs[0].id, score=70.0,
                             scored_model_version=3))
    session.flush()

    assert unscored_count(session, user.id) == len(jobs) - 1


def test_a_retrain_makes_every_score_stale_at_once(db):
    """Correct rather than wasteful: after a retrain the scores mean something different, so every
    row genuinely needs recomputing."""
    from libs.db.models import UserJobState
    from services.scorer.score_lane import unscored_count

    session, user, jobs = db
    user.model_version = 3
    for job in jobs:
        session.add(UserJobState(user_id=user.id, job_id=job.id, score=70.0,
                                 scored_model_version=3))
    session.flush()
    assert unscored_count(session, user.id) == 0

    user.model_version = 4
    session.flush()
    assert unscored_count(session, user.id) == len(jobs)


def test_the_harvest_cycle_asks_every_active_account_to_catch_up(db):
    """A corpus row is shared, so a job harvested for one user is one the others have not scored
    either — enqueuing only for users whose config matched would leave the rest behind."""
    from sqlalchemy import func, select

    from libs.db.models import QueueJob, User
    from services.harvester.cycle import _enqueue_scoring

    session, user, _ = db
    dormant = User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active",
                   active=False)
    session.add(dormant)
    session.flush()

    assert _enqueue_scoring(session) >= 1
    session.flush()

    queued = session.scalars(
        select(QueueJob.user_id).where(QueueJob.lane == "score")).all()
    assert user.id in queued
    assert dormant.id not in queued, "an inactive account must not consume a worker slot"

    # A second cycle finding nothing new must not pile up work.
    _enqueue_scoring(session)
    session.flush()
    assert session.scalar(
        select(func.count()).select_from(QueueJob)
        .where(QueueJob.lane == "score", QueueJob.user_id == user.id)) == 1


def test_an_account_without_the_old_profile_json_can_still_be_scored():
    """**Sixth instance of one assumption, and the one that broke scoring outright.**

    The keyword features are derived from `user.profile`, the JSON the capture flow wrote. An
    account built by the setup conversation has prose files and no JSON at all, so validating it
    into a `ProfileSchema` raised — dead-lettering the training run five times and then every
    scoring job for that account, each as a pydantic error buried in a queue row.

    An empty spec means the embedding carries the whole signal, which is a real model rather than a
    broken one, and strictly better than refusing to score a profile that is perfectly complete in
    the form the platform now uses.
    """
    from libs.scoring.features import spec_for

    empty = spec_for(None)
    assert empty is not None
    # And it really is empty rather than accidentally carrying somebody else's terms.
    assert not any(getattr(empty, field, None) for field in ("core_terms", "off_terms"))

    real = spec_for({"identity": {"name": "Someone"},
                     "skills": {"languages": ["python"], "frameworks": [], "domains": [],
                                "tools": []}})
    assert real is not None
