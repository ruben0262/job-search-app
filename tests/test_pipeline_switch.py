"""The pause switch, and the failure mode that made it dangerous to write naively.

Built for the re-embed — between rebuilding every vector and refitting the model there is a window
where scoring *should* refuse — and useful for every incident after it.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from libs.db.models import User
from libs.queue import enqueue
from libs.queue.queue import lane_paused

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set (no live Postgres)")


@pytest.fixture()
def db():
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


def _user(session) -> User:
    user = User(email=f"{uuid.uuid4()}@example.test", password_hash="x", status="active")
    session.add(user)
    session.flush()
    return user


def test_a_missing_switch_table_means_nothing_is_paused(db):
    """**The bug this is written around.** A missing relation aborts the transaction in Postgres,
    so the obvious `try/except` has to roll back to recover — and that throws away the caller's
    uncommitted work. It deleted the user row out from under thirty-four tests."""
    db.execute(text("DROP TABLE IF EXISTS lane_pauses"))
    user = _user(db)

    assert lane_paused(db, "score") is False
    # The session is still usable and the user is still there, which is the actual assertion.
    assert enqueue(db, "score", user.id) is not None
    assert db.get(User, user.id) is not None


def test_a_paused_lane_refuses_new_work(db):
    """Refused at the door rather than claimed and dropped. A paused lane must not accumulate work
    to process in a burst on resume — that is how a pause becomes its own incident."""
    from deploy.scripts.pipeline_switch import pause

    user = _user(db)
    pause(db, ["score"], "re-embedding")
    db.flush()

    assert lane_paused(db, "score") is True
    assert enqueue(db, "score", user.id) is None


def test_pausing_one_lane_leaves_the_others_running(db):
    from deploy.scripts.pipeline_switch import pause

    user = _user(db)
    pause(db, ["score"], "re-embedding")
    db.flush()

    assert enqueue(db, "notify", user.id) is not None


def test_resuming_lets_work_through_again(db):
    from deploy.scripts.pipeline_switch import pause, resume

    user = _user(db)
    pause(db, ["score"], "re-embedding")
    db.flush()
    resume(db, ["score"])
    db.flush()

    assert lane_paused(db, "score") is False
    assert enqueue(db, "score", user.id) is not None
