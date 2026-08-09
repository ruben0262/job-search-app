"""What a cold-start batch reports when it could not buy anything, and what the lane does about it.

The two halves of one production failure. On 2026-08-03 an account's pool came back empty for one
batch; `cold_start_batch` reported `remaining=0`, the lane read that as "this account has finished
labelling", trained, and never ran again. Six days later it was still 57 labels short of its 300,
its worker idle, and nothing had raised so much as a warning.

Reporting the truth is only half a fix, though — a lane that re-arms on `remaining` alone would
then re-queue itself forever against a pool that stays empty, which is the loop the score lane was
just taken out of. So the batch reports the shortfall, and the *re-arm* is decided by whether the
batch actually wrote anything.
"""
from __future__ import annotations

import os
import uuid

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


class NeverCalled:
    """A batch with no pool must not reach the model at all — that would be the user's money."""

    def complete_json(self, **kwargs):
        raise AssertionError("the model was called for an empty pool")


def _account(db, *, pool: int = 300):
    from libs.db.models import User
    from libs.tenant.repository import TenantRepository

    user = User(email=f"{uuid.uuid4()}@example.test", password_hash="x", status="active",
                active=True, rubric_md="score it", limits={"label_pool": pool})
    db.add(user)
    db.flush()
    TenantRepository(db).save_config(user.id, {"keywords": ["python"], "locations": ["london"]})
    db.flush()
    return user


def _labels(db, user, count: int) -> None:
    from datetime import UTC, datetime

    from libs.db.models import CorpusJob, UserLabel

    for _ in range(count):
        job = CorpusJob(url=f"https://example.com/{uuid.uuid4()}", title="Engineer",
                        source="himalayas", posted_at=datetime.now(UTC),
                        text_sha256=uuid.uuid4().hex)
        db.add(job)
        db.flush()
        db.add(UserLabel(user_id=user.id, job_id=job.id, label=50, weight=1.0, source="llm",
                         profile_generation=user.profile_generation))
    db.flush()


def test_an_empty_pool_reports_what_is_still_owed_not_zero(db):
    """`0` here meant "finished" to the caller, and the account was 57 short. It is now the truth."""
    from services.labeller.coldstart import cold_start_batch

    user = _account(db, pool=300)
    _labels(db, user, 243)

    written, remaining = cold_start_batch(db, user.id, NeverCalled())

    assert written == 0
    assert remaining == 57, "an empty pool is a statement about the corpus, not about the target"


def test_an_account_at_its_target_reports_nothing_owed(db):
    """The other side of the same number: at target, `0` is the honest answer."""
    from services.labeller.coldstart import cold_start_batch

    user = _account(db, pool=5)
    _labels(db, user, 5)

    assert cold_start_batch(db, user.id, NeverCalled()) == (0, 0)


# ── what the lane does with that number ───────────────────────────────────────────────────────

def _pending(db, lane: str, user_id) -> int:
    from sqlalchemy import func, select

    from libs.db.models import QueueJob

    return db.scalar(select(func.count()).select_from(QueueJob)
                     .where(QueueJob.lane == lane, QueueJob.user_id == user_id,
                            QueueJob.finished_at.is_(None))) or 0


def _finished_job(db, user_id):
    """A `label` job in the state the worker's follow-up runs in: claimed and being handled."""
    from libs.db.models import QueueJob

    job = QueueJob(lane="label", user_id=user_id, dedup_key="label", payload={})
    db.add(job)
    db.flush()
    return job


def test_a_batch_that_bought_nothing_does_not_queue_another(db):
    """Otherwise the honest shortfall becomes an infinite loop against a pool that stays empty.

    That is the same defect the score lane carried: a count that says work remains, driving a
    re-enqueue that cannot do any. The account is still owed labels — the harvest tick brings it
    back when the corpus has grown — but not this second, and not on this account's credential.
    """
    from services.labeller.__main__ import _queue_follow_up

    user = _account(db)
    job = _finished_job(db, user.id)

    _queue_follow_up(db, job, "cold_start", 0, 57)
    db.flush()

    assert _pending(db, "label", user.id) == 1, "only the job being handled, no follow-up"


def test_a_batch_that_bought_labels_continues_immediately(db):
    """A cold start must not wait fifteen minutes between batches while it is making progress."""
    from services.labeller.__main__ import _queue_follow_up

    user = _account(db)
    job = _finished_job(db, user.id)

    _queue_follow_up(db, job, "cold_start", 25, 57)
    db.flush()

    assert _pending(db, "label", user.id) == 2, "the one being handled, plus the next batch"


def test_an_account_that_can_train_still_does_when_its_pool_runs_dry(db):
    """A dry pool must not also cost the account its first model.

    It has enough labels to fit; what it cannot do is buy more right now. Before the fix this
    worked only by accident — the empty pool lied about `remaining` and the lie happened to reach
    `ready_to_train`. It has to hold now that the number is honest.
    """
    from services.labeller.__main__ import _queue_follow_up

    user = _account(db, pool=300)
    _labels(db, user, 243)
    job = _finished_job(db, user.id)

    _queue_follow_up(db, job, "cold_start", 0, 57)
    db.flush()

    assert _pending(db, "train", user.id) == 1
