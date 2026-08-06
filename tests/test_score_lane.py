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


# ── the pre-score centroid filter (ROADMAP 6an) ───────────────────────────────────────────────


def _loaded(user, *, centroid_blob=None, threshold=None):
    """A servable model with a stub estimator, so the lane can be driven without an artifact."""
    import numpy as np

    from libs.scoring.features import spec_for
    from services.scorer.registry import LoadedModel

    class Estimator:
        def predict(self, matrix):
            return np.full(len(matrix), 80.0, dtype=np.float32)

    return LoadedModel(
        user_id=user.id, model_version=user.model_version, profile_generation=1,
        estimator=Estimator(), task="regress", features=spec_for(None),
        embedder_model="test", embedder_dim=2, embedder_version="v1",
        cutoff=60, keep_cutoff=45,
        centroid=centroid_blob, centroid_threshold=threshold)


def _pointing(*components: float) -> bytes:
    from libs.scoring.centroid import pack

    return pack(list(components))


def test_a_row_far_from_the_users_centre_is_never_scored(db, monkeypatch):
    """**The saving is the model call, so the filter has to happen before it.**

    Measured on live data: 72% of one account's pool sits below its own threshold and none of its
    280 matches do. This asserts the mechanism actually runs — a filter that is computed and then
    ignored is the defect this project ships most often.
    """
    from sqlalchemy import select

    from libs.db.models import UserJobState
    from services.scorer import score_lane

    session, user, jobs = db
    user.model_version = 1
    # Two rows pointing the same way as the centroid, three pointing away.
    for index, job in enumerate(jobs):
        job.embedding = _pointing(1.0, 0.0) if index < 2 else _pointing(-1.0, 0.0)
    session.flush()

    model = _loaded(user, centroid_blob=_pointing(1.0, 0.0), threshold=0.5)
    monkeypatch.setattr(score_lane.ModelRegistry, "get", lambda self, uid: model)

    scored, _remaining = score_lane.score_batch(session, user.id)
    session.flush()

    assert scored == 2, "only the rows near the centre reach the model"

    states = {s.job_id: s for s in session.scalars(
        select(UserJobState).where(UserJobState.user_id == user.id))}
    assert len(states) == 5, "every row is marked at this version, or the lane re-reads it forever"
    assert sum(1 for s in states.values() if s.score is None) == 3
    assert sum(1 for s in states.values() if s.score is not None) == 2


def test_a_filtered_row_is_not_reconsidered_next_cycle(db, monkeypatch):
    """The saving is spent back if the same rows are re-examined every run. `scored_model_version`
    is what stops that, and it only moves again when the model — and with it the centroid — does."""
    from services.scorer import score_lane

    session, user, jobs = db
    user.model_version = 1
    for job in jobs:
        job.embedding = _pointing(-1.0, 0.0)
    session.flush()

    model = _loaded(user, centroid_blob=_pointing(1.0, 0.0), threshold=0.5)
    monkeypatch.setattr(score_lane.ModelRegistry, "get", lambda self, uid: model)

    first, _ = score_lane.score_batch(session, user.id)
    session.flush()
    second, _ = score_lane.score_batch(session, user.id)

    assert first == 0 and second == 0
    assert score_lane.unscored_count(session, user.id) == 0, "nothing is left pending"


def test_a_model_with_no_centroid_scores_everything(db, monkeypatch):
    """**The fail-open, and it is the one that would empty a feed if inverted.** An account in cold
    start, or a model published before the column existed, has no centre — and every row must reach
    the model rather than none of them."""
    from services.scorer import score_lane

    session, user, jobs = db
    user.model_version = 1
    for job in jobs:
        job.embedding = _pointing(-1.0, 0.0)      # as far from any centre as possible
    session.flush()

    model = _loaded(user, centroid_blob=None, threshold=None)
    monkeypatch.setattr(score_lane.ModelRegistry, "get", lambda self, uid: model)

    scored, _remaining = score_lane.score_batch(session, user.id)
    assert scored == len(jobs)


# ── the per-account pre-score filters (ROADMAP 6au) ───────────────────────────────────────────


def _configure(session, user, **config):
    from libs.db.models import UserConfig

    session.add(UserConfig(user_id=user.id, config=config))
    session.flush()


def test_a_job_outside_the_users_countries_is_never_scored(db, monkeypatch):
    """**The country filter, finally running.** `libs.corpus.geo` was written, tested forty times
    over and imported by nothing for two days, which is why American jobs kept arriving after two
    rounds of work on it (ROADMAP 6ak)."""
    from services.scorer import score_lane

    session, user, jobs = db
    user.model_version = 1
    _configure(session, user, locations=["europe"])
    for index, job in enumerate(jobs):
        job.country = "DE" if index < 2 else "US"
        job.embedding = _pointing(1.0, 0.0)
    session.flush()

    model = _loaded(user, centroid_blob=None, threshold=None)
    monkeypatch.setattr(score_lane.ModelRegistry, "get", lambda self, uid: model)

    scored, _ = score_lane.score_batch(session, user.id)
    assert scored == 2, "only the European rows reach the model"


def test_a_posting_with_no_readable_country_is_kept(db, monkeypatch):
    """**Unknown passes, and this is the half that matters.** 16% of corpus rows carry no readable
    country, and they are disproportionately the remote-first postings — the most portable jobs on
    the board would be the first thing a naive filter deleted."""
    from services.scorer import score_lane

    session, user, jobs = db
    user.model_version = 1
    _configure(session, user, locations=["europe"])
    for job in jobs:
        job.country = None
        job.embedding = _pointing(1.0, 0.0)
    session.flush()

    model = _loaded(user, centroid_blob=None, threshold=None)
    monkeypatch.setattr(score_lane.ModelRegistry, "get", lambda self, uid: model)

    scored, _ = score_lane.score_batch(session, user.id)
    assert scored == len(jobs)


def test_a_stale_posting_is_not_scored(db, monkeypatch):
    """**The user's own freshness window, applied before the model rather than after.**

    The operator's per-source setting decides how far back a *source is asked*; this is how old a
    posting may be and still be worth an account's model. Scoring a month-old job and then dropping
    it at delivery as `too_old` is the whole cost paid for nothing.
    """
    from datetime import UTC, datetime, timedelta

    from services.scorer import score_lane

    session, user, jobs = db
    user.model_version = 1
    _configure(session, user, sections=[{"platform": "all", "keywords": ["x"],
                                         "posted_within_hours": 24}])
    now = datetime.now(UTC)
    for index, job in enumerate(jobs):
        job.posted_at = now - timedelta(hours=2 if index < 2 else 200)
        job.embedding = _pointing(1.0, 0.0)
    session.flush()

    model = _loaded(user, centroid_blob=None, threshold=None)
    monkeypatch.setattr(score_lane.ModelRegistry, "get", lambda self, uid: model)

    scored, _ = score_lane.score_batch(session, user.id)
    assert scored == 2, "only the fresh rows reach the model"


def test_a_term_the_user_excluded_is_never_scored(db, monkeypatch):
    """Their own words, against title and company. "senior", or a company they will not work for."""
    from services.scorer import score_lane

    session, user, jobs = db
    user.model_version = 1
    _configure(session, user, sections=[{"platform": "all", "keywords": ["x"],
                                         "exclude_keywords": ["senior", "Acme Corp"]}])
    jobs[0].title = "Senior Backend Engineer"
    jobs[1].company = "Acme Corp"
    for job in jobs:
        job.embedding = _pointing(1.0, 0.0)
    session.flush()

    model = _loaded(user, centroid_blob=None, threshold=None)
    monkeypatch.setattr(score_lane.ModelRegistry, "get", lambda self, uid: model)

    scored, _ = score_lane.score_batch(session, user.id)
    assert scored == len(jobs) - 2, "the excluded title and the excluded company both go"


def test_the_freshest_jobs_are_scored_first(db, monkeypatch):
    """**There was no ordering at all.**

    The lane took 500 rows in whatever order Postgres returned them, so a posting found a minute ago
    queued behind a 28,000-row backlog. The product's whole claim is that a job arrives while it is
    still worth applying to.
    """
    from datetime import UTC, datetime, timedelta

    from services.scorer import score_lane

    session, user, jobs = db
    user.model_version = 1
    now = datetime.now(UTC)
    for index, job in enumerate(jobs):
        job.first_seen_at = now - timedelta(hours=index)      # jobs[0] newest
        job.embedding = _pointing(1.0, 0.0)
    session.flush()

    model = _loaded(user, centroid_blob=None, threshold=None)
    monkeypatch.setattr(score_lane.ModelRegistry, "get", lambda self, uid: model)

    score_lane.score_batch(session, user.id, limit=2)
    session.flush()

    from sqlalchemy import select

    from libs.db.models import UserJobState

    done = {s.job_id for s in session.scalars(
        select(UserJobState).where(UserJobState.user_id == user.id))}
    assert done == {jobs[0].id, jobs[1].id}, "the two newest, not two arbitrary rows"


def test_a_closed_posting_is_never_scored(db, monkeypatch):
    """There is nothing to apply for. Not a preference, so it is not in `UserFilters`."""
    from datetime import UTC, datetime

    from services.scorer import score_lane

    session, user, jobs = db
    user.model_version = 1
    jobs[0].closed_at = datetime.now(UTC)
    for job in jobs:
        job.embedding = _pointing(1.0, 0.0)
    session.flush()

    model = _loaded(user, centroid_blob=None, threshold=None)
    monkeypatch.setattr(score_lane.ModelRegistry, "get", lambda self, uid: model)

    scored, _ = score_lane.score_batch(session, user.id)
    assert scored == len(jobs) - 1


def test_an_account_with_no_config_is_not_filtered_into_silence(db, monkeypatch):
    """**The fail-open across every filter at once.** A user who has answered nothing has excluded
    nothing: no countries, no terms, and the default window. Getting this wrong empties a feed, and
    an empty feed is indistinguishable from a quiet week."""
    from services.scorer import score_lane

    session, user, jobs = db
    user.model_version = 1                     # no UserConfig row at all
    for job in jobs:
        job.embedding = _pointing(1.0, 0.0)
    session.flush()

    model = _loaded(user, centroid_blob=None, threshold=None)
    monkeypatch.setattr(score_lane.ModelRegistry, "get", lambda self, uid: model)

    scored, _ = score_lane.score_batch(session, user.id)
    assert scored == len(jobs)
