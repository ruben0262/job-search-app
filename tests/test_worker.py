"""The worker loop: a handler's writes and the job's outcome, each recorded as it happens.

There is no transaction around a handler any more. What it wrote before failing stays written, by
design: a delivery that stops part-way keeps the sends it made rather than repeating them on the retry.
"""
from __future__ import annotations

import uuid

from libs.db import find_one, insert
from libs.db.models import QueueJob, User
from libs.queue.queue import enqueue
from libs.queue.worker import Terminal, run_worker


def _account(db) -> uuid.UUID:
    user_id = insert(db, User(email=f"{uuid.uuid4()}@example.test", password_hash="x")).id
    enqueue(db, "notify", user_id)
    return user_id


def _drain(db, lane: str, handler, *, max_jobs: int = 1):
    return run_worker(db, lane, "test-worker", handler,
                      max_jobs=max_jobs, idle_sleep=0.0, stop=_stop_after(max_jobs))


def _stop_after(n: int):
    """Stop once the loop has had `n` chances to find work, so an empty lane cannot spin forever."""
    seen = {"ticks": 0}

    def stop() -> bool:
        seen["ticks"] += 1
        return seen["ticks"] > n + 1

    return stop


def _job(db, user_id) -> QueueJob:
    return find_one(db, QueueJob, {"user_id": user_id})


def _timezone(db, user_id) -> str:
    return db[User.COLLECTION].find_one({"_id": user_id})["timezone"]


def _set_timezone(db, job: QueueJob, zone: str) -> None:
    db[User.COLLECTION].update_one({"_id": job.user_id}, {"$set": {"timezone": zone}})


def test_the_handlers_effect_and_the_completion_both_land(db):
    """The invariant the whole protocol rests on: done means the work happened."""
    user_id = _account(db)

    stats = _drain(db, "notify", lambda d, job: _set_timezone(d, job, "Europe/Budapest"))

    assert stats.completed == 1
    assert _job(db, user_id).state == "done"
    assert _timezone(db, user_id) == "Europe/Budapest"


def test_a_failing_handler_keeps_what_it_wrote_and_the_job_retries(db):
    """Both halves. The write stays, by design: a delivery that stopped part-way keeps the sends it made,
    so the retry does not send them twice."""
    user_id = _account(db)

    def handler(d, job: QueueJob) -> None:
        _set_timezone(d, job, "Mars/Olympus")
        raise RuntimeError("upstream 500")

    stats = _drain(db, "notify", handler)

    assert stats.failed == 1
    job = _job(db, user_id)
    assert job.state == "ready"
    assert job.attempts == 1, "one claim, one attempt: the failure does not count twice"
    assert job.claimed_by is None, "a job waiting for its retry belongs to no worker"
    assert _timezone(db, user_id) == "Mars/Olympus", "what the handler wrote before failing stays written"


def test_the_failure_is_recorded(db):
    """Whatever the handler raised is on the job, where the operator will look for it."""
    user_id = _account(db)

    def handler(d, job: QueueJob) -> None:
        raise ValueError("telegram said no")

    stats = _drain(db, "notify", handler)

    assert "telegram said no" in _job(db, user_id).last_error
    assert stats.errors == {"ValueError": 1}


def test_a_terminal_handler_dead_letters_without_spending_the_retries(db):
    """A revoked token will never succeed."""
    user_id = _account(db)

    def handler(d, job: QueueJob) -> None:
        raise Terminal("bot was blocked by the user")

    stats = _drain(db, "notify", handler)

    assert stats.dead == 1
    job = _job(db, user_id)
    assert job.state == "dead"
    assert job.attempts == 1
    assert job.last_error == "bot was blocked by the user"


def test_an_empty_lane_is_not_an_error(db):
    """A worker with nothing to do must idle, not exit or raise. Most of a lane's life is this."""
    stats = _drain(db, "docgen", lambda d, j: None)
    assert stats.claimed == 0
