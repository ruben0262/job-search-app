"""The pause switch, and the failure mode that made it dangerous to write naively."""
from __future__ import annotations

import uuid

import pytest

from libs.db import ensure_schema, get, insert
from libs.db.models import LanePause, QueueJob, User
from libs.queue import enqueue
from libs.queue.queue import lane_paused


def _user(db) -> User:
    return insert(db, User(email=f"{uuid.uuid4()}@example.test", password_hash="x", status="active"))


def test_a_missing_switch_collection_means_nothing_is_paused(db):
    """The bug this is written around: a switch that is not there must not stop the platform."""
    db[LanePause.COLLECTION].drop()
    try:
        user = _user(db)

        assert lane_paused(db, "docgen") is False
        assert enqueue(db, "docgen", user.id) is not None
        assert get(db, User, user.id) is not None
    finally:
        ensure_schema(db)


def test_a_paused_lane_refuses_new_work(db):
    """Refused at the door rather than claimed and dropped."""
    from deploy.scripts.pipeline_switch import pause

    user = _user(db)
    pause(db, ["docgen"], "provider outage")

    assert lane_paused(db, "docgen") is True
    assert enqueue(db, "docgen", user.id) is None
    assert db[QueueJob.COLLECTION].count_documents({}) == 0


def test_pausing_one_lane_leaves_the_others_running(db):
    from deploy.scripts.pipeline_switch import pause, status

    user = _user(db)
    pause(db, ["docgen"], "provider outage")

    assert enqueue(db, "notify", user.id) is not None
    assert status(db) == {"docgen": "provider outage"}


def test_resuming_lets_work_through_again(db):
    from deploy.scripts.pipeline_switch import pause, resume

    user = _user(db)
    pause(db, ["docgen"], "provider outage")
    resume(db, ["docgen"])

    assert lane_paused(db, "docgen") is False
    assert enqueue(db, "docgen", user.id) is not None


def test_a_lane_that_no_longer_exists_is_refused_by_name():
    """`score` went with the model. Pausing it would succeed, and pause nothing."""
    from deploy.scripts.pipeline_switch import main

    with pytest.raises(SystemExit, match="unknown lane"):
        main(["pause", "score"])
