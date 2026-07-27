"""T5, T6, T7 — the queue's three release-blocking properties.

Every test here is one the plan calls out as a failure that is silent in production:

- **T7 claim-once.** Two workers, one job. A double claim is a duplicate notification or a duplicate
  retrain, and nothing reports it.
- **T6 fairness.** One user with a backlog must not occupy the trainer.
- **T5 restart safety.** A worker killed mid-job must cost one retry, never a lost job — and a
  user's feed must not stall forever because one delivery failed.

Concurrency is exercised with two real connections, not by asserting on the SQL string. `SKIP
LOCKED` either works against Postgres or it does not, and the version that reads correctly while
double-claiming is exactly the one worth catching.
"""
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
    """The property `SKIP LOCKED` exists for. Two real connections, because a single-session test
    would pass against a broken query — the row is not contended.

    A double claim is a duplicate notification or a duplicate retrain. Nothing reports it.
    """
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
    """Lane isolation. Separate worker pools are the whole design — a `notify` worker picking up a
    training job would block the feed behind a CPU-bound fit (`SCHEDULER_PLAN.md` §1)."""
    user = _user(ctx)
    enqueue(ctx, "train", user.id, dedup_key="retrain")
    ctx.flush()

    assert claim(ctx, "notify", "w") is None
    assert claim(ctx, "docgen", "w") is None
    assert claim(ctx, "train", "w") is not None


def test_a_second_identical_job_collapses_into_the_pending_one(ctx):
    """The debounce, enforced by the database rather than by application logic that can race. Five
    labels arriving at once must produce exactly one retrain."""
    user = _user(ctx)
    first = enqueue(ctx, "train", user.id, dedup_key="retrain")
    ctx.flush()
    others = [enqueue(ctx, "train", user.id, dedup_key="retrain") for _ in range(4)]

    assert first is not None
    assert others == [None] * 4


def test_a_new_job_can_be_enqueued_once_the_previous_one_finished(ctx):
    """The collapse index is partial on the live states. If it covered `done` too, a user could
    never be retrained twice."""
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
    """A worker killed mid-job costs one retry, never a lost job. Without the sweeper the row stays
    `claimed` forever and that user's lane is silently stuck."""
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
    """A counter that only moved on a *reported* failure would never advance for a worker that is
    killed before it can report."""
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
        states.append(fail(ctx, job, "upstream 500"))
        ctx.flush()
        ctx.execute(text("UPDATE jobs SET run_after = now() WHERE id = :i"), {"i": job.id})

    assert states == ["ready", "ready", "ready", "ready", "dead"]
    assert claim(ctx, "notify", "w") is None


def test_a_terminal_condition_dead_letters_immediately(ctx):
    """A revoked Telegram token will never succeed. Spending five attempts on it wastes the retries
    and then goes quiet anyway — the worst of both (`SCHEDULER_PLAN.md` §6, G6)."""
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
    """Round-robin by last completion. Under FIFO, a user who enqueued first is served first
    forever, and a second user's model simply never retrains."""
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
    """`notify` and `docgen` are naturally spread, so the fairness subquery would only add latency.
    Ordering there must remain oldest-first."""
    user = _user(ctx)
    first = enqueue(ctx, "docgen", user.id, payload={"job_id": 1})
    ctx.flush()
    ctx.execute(text("UPDATE jobs SET run_after = now() - interval '5 minutes' WHERE id = :i"),
                {"i": first})
    enqueue(ctx, "docgen", user.id, payload={"job_id": 2})
    ctx.flush()

    assert claim(ctx, "docgen", "w").id == first


def test_a_delayed_job_is_not_claimable_yet(ctx):
    """The debounce window. A retrain enqueued with a 10-minute delay must not be picked up
    immediately, or the burst it exists to collapse is retrained once per label anyway."""
    user = _user(ctx)
    enqueue(ctx, "train", user.id, dedup_key="retrain", delay_min=10)
    ctx.flush()
    assert claim(ctx, "train", "w") is None


# ── the notify dispatcher ─────────────────────────────────────────────────────────────────────

def test_the_feed_re_arms_at_enqueue_not_at_completion(ctx):
    """The single most likely silent failure on the platform: a user's feed stops forever because
    one delivery errored. `next_run_at` advances when the job is *created*, so a failed or slow
    delivery cannot stall the next one (`SCHEDULER_PLAN.md` §4)."""
    user = _user(ctx, active=True, status="active", telegram_status="connected",
                 notify_interval_min=60)
    ctx.execute(text("UPDATE users SET next_run_at = now() - interval '1 minute' WHERE id = :i"),
                {"i": user.id})
    ctx.expire(user)

    assert dispatch_due_notifications(ctx) == 1
    ctx.flush()
    ctx.refresh(user)

    job = claim(ctx, "notify", "w")
    fail(ctx, job, "telegram timeout")     # the delivery failed …
    ctx.flush()

    assert user.next_run_at is not None
    assert dispatch_due_notifications(ctx) == 0, "…but the slot already moved, so nothing re-fires"


def test_a_user_whose_bot_is_broken_is_not_enqueued(ctx):
    """Retrying a revoked token cannot succeed. The fix is a reconnect email, not a queue entry."""
    user = _user(ctx, active=True, status="active", telegram_status="broken")
    ctx.execute(text("UPDATE users SET next_run_at = now() - interval '1 minute' WHERE id = :i"),
                {"i": user.id})
    assert dispatch_due_notifications(ctx) == 0


def test_a_dormant_user_is_not_enqueued(ctx):
    """G11: an abandoned account must stop costing anything, including delivery attempts."""
    user = _user(ctx, active=True, status="dormant", telegram_status="connected")
    ctx.execute(text("UPDATE users SET next_run_at = now() - interval '1 minute' WHERE id = :i"),
                {"i": user.id})
    assert dispatch_due_notifications(ctx) == 0
