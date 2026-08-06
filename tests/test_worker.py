"""The worker loop's one real invariant: the handler's effect and the job's completion are atomic."""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from libs.db.models import QueueJob, User
from libs.queue.queue import enqueue
from libs.queue.worker import Terminal, run_worker

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (no live Postgres)")


@pytest.fixture()
def stack():
    """A real engine, because the worker commits — a rolled-back outer transaction would hide it."""
    engine = create_engine(os.environ["DATABASE_URL"])
    factory = sessionmaker(engine)
    created: list[uuid.UUID] = []

    def make_user() -> uuid.UUID:
        with factory() as s:
            user = User(email=f"{uuid.uuid4()}@example.test", password_hash="x")
            s.add(user)
            s.commit()
            created.append(user.id)
            return user.id

    try:
        yield factory, make_user
    finally:
        with factory() as s:
            for user_id in created:
                s.execute(text("DELETE FROM jobs WHERE user_id = :u"), {"u": user_id})
                s.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
            s.commit()


def _drain(factory, lane: str, handler, *, max_jobs: int = 1):
    return run_worker(factory, lane, "test-worker", handler,
                      max_jobs=max_jobs, idle_sleep=0.0, stop=_stop_after(max_jobs))


def _stop_after(n: int):
    """Stop once the loop has had `n` chances to find work, so an empty lane cannot spin forever."""
    seen = {"ticks": 0}

    def stop() -> bool:
        seen["ticks"] += 1
        return seen["ticks"] > n + 1

    return stop


def _state(factory, user_id) -> str:
    with factory() as s:
        return s.scalar(text("SELECT state FROM jobs WHERE user_id = :u").bindparams(u=user_id))


def test_the_handlers_effect_and_the_completion_commit_together(stack):
    """The invariant the whole protocol rests on."""
    factory, make_user = stack
    user_id = make_user()
    enqueue_in(factory, "notify", user_id)

    def handler(session: Session, job: QueueJob) -> None:
        session.execute(text("UPDATE users SET timezone = 'Europe/Budapest' WHERE id = :u"),
                        {"u": job.user_id})

    stats = _drain(factory, "notify", handler)

    assert stats.completed == 1
    assert _state(factory, user_id) == "done"
    with factory() as s:
        assert s.scalar(text("SELECT timezone FROM users WHERE id = :u")
                        .bindparams(u=user_id)) == "Europe/Budapest"


def test_a_failing_handler_rolls_back_its_effect_and_the_job_retries(stack):
    """Both halves."""
    factory, make_user = stack
    user_id = make_user()
    enqueue_in(factory, "notify", user_id)

    def handler(session: Session, job: QueueJob) -> None:
        session.execute(text("UPDATE users SET timezone = 'Mars/Olympus' WHERE id = :u"),
                        {"u": job.user_id})
        raise RuntimeError("upstream 500")

    stats = _drain(factory, "notify", handler)

    assert stats.failed == 1
    assert _state(factory, user_id) == "ready"
    with factory() as s:
        # The rollback that undid the handler's write also undid the claim.
        assert s.scalar(text("SELECT attempts FROM jobs WHERE user_id = :u")
                        .bindparams(u=user_id)) == 1
    with factory() as s:
        assert s.scalar(text("SELECT timezone FROM users WHERE id = :u")
                        .bindparams(u=user_id)) == "UTC", "the failed write must not survive"


def test_the_failure_is_recorded_even_though_the_handlers_transaction_was_poisoned(stack):
    """The failure has to be written in a fresh transaction."""
    factory, make_user = stack
    user_id = make_user()
    enqueue_in(factory, "notify", user_id)

    def handler(session: Session, job: QueueJob) -> None:
        raise ValueError("telegram said no")

    _drain(factory, "notify", handler)

    with factory() as s:
        error = s.scalar(text("SELECT last_error FROM jobs WHERE user_id = :u").bindparams(u=user_id))
    assert "telegram said no" in error


def test_a_terminal_handler_dead_letters_without_spending_the_retries(stack):
    """A revoked token, or a retrain that is not owed."""
    factory, make_user = stack
    user_id = make_user()
    enqueue_in(factory, "notify", user_id)

    def handler(session: Session, job: QueueJob) -> None:
        raise Terminal("bot was blocked by the user")

    stats = _drain(factory, "notify", handler)

    assert stats.dead == 1
    assert _state(factory, user_id) == "dead"
    with factory() as s:
        attempts = s.scalar(text("SELECT attempts FROM jobs WHERE user_id = :u").bindparams(u=user_id))
    assert attempts == 1


def test_an_empty_lane_is_not_an_error(stack):
    """A worker with nothing to do must idle, not exit or raise. Most of a lane's life is this."""
    factory, _ = stack
    stats = _drain(factory, "docgen", lambda s, j: None)
    assert stats.claimed == 0


def enqueue_in(factory, lane: str, user_id) -> None:
    with factory() as s:
        enqueue(s, lane, user_id)
        s.commit()
