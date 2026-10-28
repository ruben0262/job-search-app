"""A counter and the worker it drives must ask the same question.

Three separate faults in one week, all this shape, all invisible:

- The pipeline page counted rows "waiting to be embedded" that the embedder skips on purpose, so
  two closed postings sat in the Embedded stage for 25 hours reading as a stall while the embedder
  was idle and correct.
- The same page counted rows waiting to be described that the describer would never fetch.
- `unscored_count` omitted the pre-score filter while `_candidates` applied it, so the score lane
  read `remaining=2883`, re-enqueued itself, found two rows, and repeated — 2,326 completed jobs
  against a backlog that never moved.

The third is the one that shows why this deserves a guard rather than three fixes. A wrong count on
a page is a misleading number. A wrong count that drives a **decision** — re-enqueue, retry, alert —
is a loop, and it spends a user's own credential while looking healthy.

None of these raise. Every component is individually correct; the fault is between them. So the
test is not "does this function work" but "do these two agree", which is the only place it exists.
"""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("sqlalchemy")

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (no live Postgres)")


@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        conn.close()


def _account(db):
    from libs.db.models import User

    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active",
                active=True, model_version=1)
    db.add(user)
    db.flush()
    return user


def _job(db, **kwargs):
    from libs.db.models import CorpusJob

    kwargs.setdefault("source", "himalayas")
    kwargs.setdefault("embedding", b"\x00" * 16)
    kwargs.setdefault("text_sha256", uuid.uuid4().hex)
    kwargs.setdefault("embedding_dim", 4)
    kwargs.setdefault("posted_at", datetime.now(UTC))
    row = CorpusJob(url=f"https://example.com/{uuid.uuid4()}", title="Engineer", **kwargs)
    db.add(row)
    db.flush()
    return row


# ── the scorer: the counter that drove a loop ─────────────────────────────────────────────────

def test_the_scorer_counts_only_what_it_could_actually_score(db):
    """`remaining` decides whether the lane runs again, so it must mean what the lane can reach."""
    from libs.tenant.filters import filters_for
    from services.scorer.score_lane import _candidates, unscored_count

    user = _account(db)
    reachable = _job(db)
    # Each of these is excluded by `candidate_conditions` and must not inflate the count.
    _job(db, closed_at=datetime.now(UTC))
    _job(db, describe_failures=5)
    _job(db, posted_at=datetime.now(UTC) - timedelta(days=400))
    db.flush()

    counted = unscored_count(db, user.id)
    fetched = _candidates(db, user, user.id, filters_for(db, user), 500)

    assert counted == len(fetched), (
        f"the counter says {counted} and the fetcher finds {len(fetched)}; that gap is what made "
        "the score lane re-enqueue itself forever")
    assert {row["id"] for row in fetched} == {reachable.id}


def test_a_lane_with_nothing_reachable_reports_nothing_remaining(db):
    """Zero is what stops the self-continuation. A non-zero count here is the loop."""
    from services.scorer.score_lane import unscored_count

    user = _account(db)
    _job(db, closed_at=datetime.now(UTC))
    _job(db, describe_failures=5)
    db.flush()

    assert unscored_count(db, user.id) == 0


# ── the pipeline page: counters that drove an operator, not a loop ────────────────────────────

def test_the_page_and_the_describer_count_the_same_rows(db):
    """Same shape, same guard: a closed row is never fetched and must not read as waiting."""
    from libs import settings
    from libs.corpus.repository import CorpusRepository
    from libs.ops.pipeline import pipeline

    _job(db, description=None, embedding=None, text_sha256=None, embedding_dim=None,
         closed_at=datetime.now(UTC))
    db.flush()

    page = {s["key"]: s for s in pipeline(db, hours=24)["stages"]}["describe"]["waiting"]
    worker = len(CorpusRepository(db).awaiting_description(
        limit=5000, max_age_hours=int(settings.get(db, "job_max_age_hours"))))

    assert page == worker


# ── delivery: the counter that showed a phantom backlog to a user ─────────────────────────────

def test_the_status_page_and_the_notifier_count_the_same_matches(db):
    """A retired match is one delivery gave up on. Counting it told a user 99 were waiting."""
    from sqlalchemy import func, select

    from libs.db.models import UserJobState
    from libs.tenant.filters import pending_match_conditions

    user = _account(db)
    job = _job(db)
    db.add(UserJobState(user_id=user.id, job_id=job.id, score=95.0,
                        scored_at=datetime.now(UTC), notified_at=None,
                        retired_at=datetime.now(UTC)))
    db.flush()

    still_owed = db.scalar(
        select(func.count()).select_from(UserJobState)
        .where(UserJobState.user_id == user.id, *pending_match_conditions(50.0)))

    assert still_owed == 0, "a match the notifier abandoned is not one the user is waiting for"
