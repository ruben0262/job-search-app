"""T5, T6, T7 — the queue's three release-blocking properties."""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from libs.db.models import QueueJob, User
from libs.queue.queue import (
    claim,
    complete,
    dispatch_due_notifications,
    enqueue,
    fail,
    sweep_expired,
    terminate,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (no live Postgres)")


@pytest.fixture()
def ctx():
    """A rolled-back transaction. Committing would leave queue rows other tests would claim."""
    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    s = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield s
    finally:
        s.close()
        trans.rollback()
        conn.close()


def _user(s, **kw) -> User:
    user = User(email=f"{uuid.uuid4()}@example.test", password_hash="x", **kw)
    s.add(user)
    s.flush()
    return user


# ── T7: claim-once, and lanes do not leak into each other ─────────────────────────────────────

def test_two_concurrent_workers_never_claim_the_same_job():
    """The property `SKIP LOCKED` exists for."""
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("needs Postgres")
    engine = create_engine(os.environ["DATABASE_URL"])

    with Session(engine) as setup:
        user = _user(setup)
        enqueue(setup, "train", user.id, dedup_key="retrain")
        setup.commit()
        user_id = user.id

    try:
        with Session(engine) as a, Session(engine) as b:
            first = claim(a, "train", "worker-a")
            second = claim(b, "train", "worker-b")
            assert first is not None
            assert second is None, "the second worker must skip a locked row, not block on it"
            a.commit()
            b.commit()
    finally:
        with Session(engine) as cleanup:
            cleanup.execute(text("DELETE FROM jobs WHERE user_id = :u"), {"u": user_id})
            cleanup.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
            cleanup.commit()


def test_claiming_one_lane_never_returns_another_lanes_job(ctx):
    """Lane isolation."""
    user = _user(ctx)
    enqueue(ctx, "train", user.id, dedup_key="retrain")
    ctx.flush()

    assert claim(ctx, "notify", "w") is None
    assert claim(ctx, "docgen", "w") is None
    assert claim(ctx, "train", "w") is not None


def test_a_second_identical_job_collapses_into_the_pending_one(ctx):
    """The debounce, enforced by the database rather than by application logic that can race."""
    user = _user(ctx)
    first = enqueue(ctx, "train", user.id, dedup_key="retrain")
    ctx.flush()
    others = [enqueue(ctx, "train", user.id, dedup_key="retrain") for _ in range(4)]

    assert first is not None
    assert others == [None] * 4


def test_a_new_job_can_be_enqueued_once_the_previous_one_finished(ctx):
    """The collapse index is partial on the live states."""
    user = _user(ctx)
    enqueue(ctx, "train", user.id, dedup_key="retrain")
    ctx.flush()
    job = claim(ctx, "train", "w")
    complete(ctx, job)
    ctx.flush()

    assert enqueue(ctx, "train", user.id, dedup_key="retrain") is not None


def test_jobs_without_a_dedup_key_are_not_collapsed(ctx):
    """Two CV builds for different jobs are two pieces of work, not a duplicate."""
    user = _user(ctx)
    assert enqueue(ctx, "docgen", user.id, payload={"job_id": 1}) is not None
    ctx.flush()
    assert enqueue(ctx, "docgen", user.id, payload={"job_id": 2}) is not None


# ── T5: restart safety ────────────────────────────────────────────────────────────────────────

def test_an_expired_claim_returns_to_ready(ctx):
    """A worker killed mid-job costs one retry, never a lost job."""
    user = _user(ctx)
    enqueue(ctx, "notify", user.id)
    ctx.flush()
    job = claim(ctx, "notify", "worker-that-died")
    assert claim(ctx, "notify", "another") is None

    # `notify`'s visibility timeout is 2 minutes; age the claim past it rather than sleeping.
    ctx.execute(text("UPDATE jobs SET claimed_at = now() - interval '10 minutes' WHERE id = :i"),
                {"i": job.id})
    assert sweep_expired(ctx, "notify") == 1

    recovered = claim(ctx, "notify", "worker-b")
    assert recovered is not None and recovered.id == job.id
    assert recovered.attempts == 2, "the retry counts, so a job that always kills its worker dies"


def test_attempts_increment_on_claim_so_a_dying_worker_cannot_retry_forever(ctx):
    """A counter that only moved on a *reported* failure would never advance for a worker that is killed."""
    user = _user(ctx)
    enqueue(ctx, "notify", user.id)
    ctx.flush()
    assert claim(ctx, "notify", "w").attempts == 1


def test_a_repeatedly_failing_job_dead_letters_rather_than_retrying_forever(ctx):
    user = _user(ctx)
    enqueue(ctx, "notify", user.id)
    ctx.flush()

    states = []
    for _ in range(5):
        job = claim(ctx, "notify", "w")
        # `retry_in_min=0` so the retry is immediately claimable.
        states.append(fail(ctx, job, "upstream 500", retry_in_min=0))
        ctx.flush()

    assert states == ["ready", "ready", "ready", "ready", "dead"]
    assert claim(ctx, "notify", "w") is None


def test_a_terminal_condition_dead_letters_immediately(ctx):
    """A revoked Telegram token will never succeed."""
    user = _user(ctx)
    enqueue(ctx, "notify", user.id)
    ctx.flush()
    job = claim(ctx, "notify", "w")

    terminate(ctx, job, "telegram 403: bot was blocked by the user")
    ctx.flush()
    assert ctx.get(QueueJob, job.id).state == "dead"
    assert job.attempts == 1, "a terminal condition must not consume the retry budget"


# ── T6: fairness ──────────────────────────────────────────────────────────────────────────────

def test_one_user_with_a_backlog_cannot_occupy_the_trainer(ctx):
    """Round-robin by last completion."""
    hog, other = _user(ctx), _user(ctx)

    # The hog has been served recently; the other user never has.
    enqueue(ctx, "label", hog.id, dedup_key="a")
    ctx.flush()
    complete(ctx, claim(ctx, "label", "w"))
    ctx.flush()

    enqueue(ctx, "label", hog.id, dedup_key="b")
    ctx.flush()
    enqueue(ctx, "label", other.id, dedup_key="b")
    ctx.flush()

    served = claim(ctx, "label", "w")
    assert served.user_id == other.id, "the user who has waited longest goes first"


def test_fifo_lanes_stay_fifo(ctx):
    """`notify` and `docgen` are naturally spread, so the fairness subquery would only add latency."""
    user = _user(ctx)
    first = enqueue(ctx, "docgen", user.id, payload={"job_id": 1})
    ctx.flush()
    ctx.execute(text("UPDATE jobs SET run_after = now() - interval '5 minutes' WHERE id = :i"),
                {"i": first})
    enqueue(ctx, "docgen", user.id, payload={"job_id": 2})
    ctx.flush()

    assert claim(ctx, "docgen", "w").id == first


def test_a_delayed_job_is_not_claimable_yet(ctx):
    """The debounce window."""
    user = _user(ctx)
    enqueue(ctx, "train", user.id, dedup_key="retrain", delay_min=10)
    ctx.flush()
    assert claim(ctx, "train", "w") is None


# ── the notify dispatcher ─────────────────────────────────────────────────────────────────────

def _posting(ctx, *, minutes_ago: int = 0):
    """A posting stored `minutes_ago`: what makes an account worth asking again, now nothing scores."""
    from datetime import UTC, datetime, timedelta

    from libs.db.models import CorpusJob

    job = CorpusJob(url=f"https://example.test/{uuid.uuid4().hex}", source="greenhouse",
                    title="Backend Engineer",
                    first_seen_at=datetime.now(UTC) - timedelta(minutes=minutes_ago))
    ctx.add(job)
    ctx.flush()
    return job


def _finished_run(ctx, user, *, minutes_ago: int) -> None:
    from datetime import UTC, datetime, timedelta

    ctx.add(QueueJob(lane="notify", user_id=user.id, state="done", dedup_key="due",
                     finished_at=datetime.now(UTC) - timedelta(minutes=minutes_ago)))
    ctx.flush()


def test_an_account_the_corpus_has_grown_for_is_queued(ctx):
    """Nothing scores any more, so a posting stored since its last run is what makes one worth it."""
    user = _user(ctx, active=True, status="active", telegram_status="connected")
    _finished_run(ctx, user, minutes_ago=30)
    _posting(ctx, minutes_ago=5)

    assert dispatch_due_notifications(ctx) == 1


def test_an_account_with_nothing_new_since_its_last_run_is_not_queued(ctx):
    """There is no cadence to fire on, so an unchanged corpus means an idle lane."""
    user = _user(ctx, active=True, status="active", telegram_status="connected")
    _posting(ctx, minutes_ago=30)
    _finished_run(ctx, user, minutes_ago=5)

    assert dispatch_due_notifications(ctx) == 0, (
        "a notify run with nothing new to look at is a claim, a query and a log line for no reason")


def test_the_sweep_does_not_queue_a_second_run_while_one_is_pending(ctx):
    """The dedup key is what stops forty postings becoming forty runs."""
    _user(ctx, active=True, status="active", telegram_status="connected")
    _posting(ctx)

    assert dispatch_due_notifications(ctx) == 1
    ctx.flush()
    assert dispatch_due_notifications(ctx) == 0


def test_an_account_reachable_only_by_its_browser_is_queued(ctx):
    """Telegram is optional: a browser that asked for notifications is a channel on its own."""
    from libs.db.models import PushSubscription

    user = _user(ctx, active=True, status="active", telegram_status="unconfigured")
    _posting(ctx)
    assert dispatch_due_notifications(ctx) == 0, "the premise: no channel, no run"

    ctx.add(PushSubscription(user_id=user.id, endpoint=f"https://push.example.test/{uuid.uuid4()}",
                             p256dh="k", auth="a"))
    ctx.flush()
    assert dispatch_due_notifications(ctx) == 1


def test_a_user_whose_bot_is_broken_is_not_enqueued(ctx):
    """Retrying a revoked token cannot succeed. The fix is a reconnect email, not a queue entry."""
    _user(ctx, active=True, status="active", telegram_status="broken")
    _posting(ctx)
    assert dispatch_due_notifications(ctx) == 0


def test_a_suspended_user_is_not_enqueued(ctx):
    """A stopped account must stop costing anything, including delivery attempts."""
    _user(ctx, active=True, status="suspended", telegram_status="connected")
    _posting(ctx)
    assert dispatch_due_notifications(ctx) == 0


def test_an_unscheduled_account_still_obeys_every_other_gate(ctx):
    """Reading NULL as "due" must not become "due regardless of anything else"."""
    _user(ctx, active=True, status="suspended", telegram_status="connected")
    _user(ctx, active=True, status="active", telegram_status="broken")
    _user(ctx, active=False, status="active", telegram_status="connected")
    _posting(ctx)

    assert dispatch_due_notifications(ctx) == 0


def test_every_lane_has_a_claim_timeout():
    """A lane missing from the map crashes its worker on the first sweep, not on the first job."""
    from libs.db.models.queue import CLAIM_TIMEOUT_MIN, LANES

    assert set(LANES) == set(CLAIM_TIMEOUT_MIN), (
        "every lane needs a claim timeout and every timeout needs a lane")


def test_a_finished_job_records_when_it_actually_finished(ctx):
    """`now()` is transaction start time in Postgres."""
    import time
    import uuid

    from libs.db.models import User
    from libs.queue import claim, complete, enqueue

    owner = User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active")
    ctx.add(owner)
    ctx.flush()

    # The fixture rolls back rather than commits, so this flushes.
    enqueue(ctx, "score", owner.id, payload={})
    ctx.flush()

    job = claim(ctx, "score", worker="test")
    assert job is not None
    time.sleep(0.05)
    complete(ctx, job)
    ctx.flush()
    ctx.refresh(job)

    assert job.finished_at > job.claimed_at, "a job that took time must record that it took time"
