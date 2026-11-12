"""T5, T6, T7 — the queue's three release-blocking properties."""
from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from libs.db import get, insert, now
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


def _user(db, **kw) -> User:
    return insert(db, User(email=f"{uuid.uuid4()}@example.test", password_hash="x",
                           subscription_status="active", **kw))


def _age(db, job_id: int, **fields) -> None:
    """Move a job's clocks into the past rather than sleeping."""
    db[QueueJob.COLLECTION].update_one(
        {"_id": job_id}, {"$set": {name: now() - ago for name, ago in fields.items()}})


# ── T7: claim-once, and lanes do not leak into each other ─────────────────────────────────────

def test_two_concurrent_workers_never_claim_the_same_job(db):
    """The property the one atomic find-and-modify exists for: racing workers share no job and lose none."""
    user = _user(db)
    enqueued = {enqueue(db, "docgen", user.id, payload={"job_id": n}) for n in range(20)}

    def drain(worker: str) -> list[int]:
        taken = []
        while (job := claim(db, "docgen", worker)) is not None:
            taken.append(job.id)
        return taken

    with ThreadPoolExecutor(max_workers=8) as pool:
        claimed = [job_id for taken in pool.map(drain, [f"worker-{n}" for n in range(8)])
                   for job_id in taken]

    assert len(claimed) == len(set(claimed)), "two workers claimed the same job"
    assert set(claimed) == enqueued, "every job is claimed by somebody"


def test_claiming_one_lane_never_returns_another_lanes_job(db):
    """Lane isolation."""
    user = _user(db)
    enqueue(db, "docgen", user.id)

    assert claim(db, "notify", "w") is None
    assert claim(db, "docgen", "w") is not None


def test_a_lane_that_does_not_exist_is_refused_rather_than_stored(db):
    """A typo, or a lane removed with the model, must fail loudly: a job nobody claims is silent."""
    user = _user(db)
    for lane in ("train", "notfy"):
        with pytest.raises(ValueError, match="not one of"):
            enqueue(db, lane, user.id)
    assert db[QueueJob.COLLECTION].count_documents({}) == 0


def test_a_second_identical_job_collapses_into_the_pending_one(db):
    """The debounce, enforced by the database rather than by application logic that can race."""
    user = _user(db)
    first = enqueue(db, "notify", user.id, dedup_key="due")
    others = [enqueue(db, "notify", user.id, dedup_key="due") for _ in range(4)]

    assert first is not None
    assert others == [None] * 4
    assert db[QueueJob.COLLECTION].count_documents({"user_id": user.id}) == 1


def test_a_claimed_job_still_collapses_a_new_identical_one(db):
    """Work in progress is pending too: a second run behind it would repeat what it is doing."""
    user = _user(db)
    enqueue(db, "notify", user.id, dedup_key="due")
    claim(db, "notify", "w")

    assert enqueue(db, "notify", user.id, dedup_key="due") is None


def test_a_new_job_can_be_enqueued_once_the_previous_one_finished(db):
    """The collapse index is partial on the live states."""
    user = _user(db)
    enqueue(db, "notify", user.id, dedup_key="due")
    complete(db, claim(db, "notify", "w"))

    assert enqueue(db, "notify", user.id, dedup_key="due") is not None


def test_jobs_without_a_dedup_key_are_not_collapsed(db):
    """Two CV builds for different jobs are two pieces of work, not a duplicate."""
    user = _user(db)
    assert enqueue(db, "docgen", user.id, payload={"job_id": 1}) is not None
    assert enqueue(db, "docgen", user.id, payload={"job_id": 2}) is not None


# ── T5: restart safety ────────────────────────────────────────────────────────────────────────

def test_an_expired_claim_returns_to_ready(db):
    """A worker killed mid-job costs one retry, never a lost job."""
    user = _user(db)
    enqueue(db, "notify", user.id)
    job = claim(db, "notify", "worker-that-died")
    assert claim(db, "notify", "another") is None

    # `notify`'s visibility timeout is 2 minutes; age the claim past it rather than sleeping.
    _age(db, job.id, claimed_at=timedelta(minutes=10))
    assert sweep_expired(db, "notify") == 1

    recovered = claim(db, "notify", "worker-b")
    assert recovered is not None and recovered.id == job.id
    assert recovered.attempts == 2, "the retry counts, so a job that always kills its worker dies"


def test_a_live_claim_is_not_swept(db):
    """The sweep returns only what has outlived its lane's timeout, or two workers run one job."""
    user = _user(db)
    enqueue(db, "notify", user.id)
    job = claim(db, "notify", "worker-a")
    _age(db, job.id, claimed_at=timedelta(minutes=1))

    assert sweep_expired(db, "notify") == 0
    assert get(db, QueueJob, job.id).state == "claimed"


def test_attempts_increment_on_claim_so_a_dying_worker_cannot_retry_forever(db):
    """A counter that only moved on a *reported* failure would never advance for a worker that is killed."""
    user = _user(db)
    enqueue(db, "notify", user.id)
    assert claim(db, "notify", "w").attempts == 1


def test_a_repeatedly_failing_job_dead_letters_rather_than_retrying_forever(db):
    user = _user(db)
    enqueue(db, "notify", user.id)

    states = []
    for _ in range(5):
        job = claim(db, "notify", "w")
        # `retry_in_min=0` so the retry is immediately claimable.
        states.append(fail(db, job, "upstream 500", retry_in_min=0))

    assert states == ["ready", "ready", "ready", "ready", "dead"]
    assert claim(db, "notify", "w") is None
    assert get(db, QueueJob, job.id).last_error == "upstream 500"


def test_a_terminal_condition_dead_letters_immediately(db):
    """A revoked Telegram token will never succeed."""
    user = _user(db)
    enqueue(db, "notify", user.id)
    job = claim(db, "notify", "w")

    terminate(db, job, "telegram 403: bot was blocked by the user")

    stored = get(db, QueueJob, job.id)
    assert stored.state == "dead"
    assert stored.attempts == 1, "a terminal condition must not consume the retry budget"


# ── T6: order ─────────────────────────────────────────────────────────────────────────────────

def test_fifo_lanes_stay_fifo(db):
    """`notify` and `docgen` are naturally spread, so the oldest due job goes first."""
    user = _user(db)
    second = enqueue(db, "docgen", user.id, payload={"job_id": 2})
    first = enqueue(db, "docgen", user.id, payload={"job_id": 1})
    _age(db, first, run_after=timedelta(minutes=5))

    assert [claim(db, "docgen", "w").id for _ in range(2)] == [first, second]


def test_a_delayed_job_is_not_claimable_yet(db):
    """The debounce window."""
    user = _user(db)
    enqueue(db, "notify", user.id, dedup_key="due", delay_min=10)
    assert claim(db, "notify", "w") is None


# ── the notify dispatcher ─────────────────────────────────────────────────────────────────────

def _posting(db, *, minutes_ago: int = 0):
    """A posting stored `minutes_ago`: what makes an account worth asking again, now nothing scores."""
    from libs.db.models import CorpusJob

    return insert(db, CorpusJob(url=f"https://example.test/{uuid.uuid4().hex}", source="greenhouse",
                                title="Backend Engineer",
                                first_seen_at=now() - timedelta(minutes=minutes_ago)))


def _finished_run(db, user, *, minutes_ago: int) -> None:
    insert(db, QueueJob(lane="notify", user_id=user.id, state="done", dedup_key="due",
                        finished_at=now() - timedelta(minutes=minutes_ago)))


def test_an_account_the_corpus_has_grown_for_is_queued(db):
    """Nothing scores any more, so a posting stored since its last run is what makes one worth it."""
    user = _user(db, active=True, status="active", telegram_status="connected")
    _finished_run(db, user, minutes_ago=30)
    _posting(db, minutes_ago=5)

    assert dispatch_due_notifications(db) == 1
    queued = db[QueueJob.COLLECTION].find_one({"user_id": user.id, "state": "ready"})
    assert queued["lane"] == "notify"


def test_an_account_with_nothing_new_since_its_last_run_is_not_queued(db):
    """There is no cadence to fire on, so an unchanged corpus means an idle lane."""
    user = _user(db, active=True, status="active", telegram_status="connected")
    _posting(db, minutes_ago=30)
    _finished_run(db, user, minutes_ago=5)

    assert dispatch_due_notifications(db) == 0, (
        "a notify run with nothing new to look at is a claim, a query and a log line for no reason")


def test_an_empty_corpus_queues_nobody(db):
    """Before the first harvest there is nothing any account could be told."""
    _user(db, active=True, status="active", telegram_status="connected")
    assert dispatch_due_notifications(db) == 0


def test_the_sweep_does_not_queue_a_second_run_while_one_is_pending(db):
    """The dedup key is what stops forty postings becoming forty runs."""
    _user(db, active=True, status="active", telegram_status="connected")
    _posting(db)

    assert dispatch_due_notifications(db) == 1
    assert dispatch_due_notifications(db) == 0


def test_an_account_reachable_only_by_its_browser_is_queued(db):
    """Telegram is optional: a browser that asked for notifications is a channel on its own."""
    from libs.db.models import PushSubscription

    user = _user(db, active=True, status="active", telegram_status="unconfigured")
    _posting(db)
    assert dispatch_due_notifications(db) == 0, "the premise: no channel, no run"

    insert(db, PushSubscription(user_id=user.id, endpoint=f"https://push.example.test/{uuid.uuid4()}",
                                p256dh="k", auth="a"))
    assert dispatch_due_notifications(db) == 1


def test_a_user_whose_bot_is_broken_is_not_enqueued(db):
    """Retrying a revoked token cannot succeed. The fix is a reconnect email, not a queue entry."""
    _user(db, active=True, status="active", telegram_status="broken")
    _posting(db)
    assert dispatch_due_notifications(db) == 0


def test_a_suspended_user_is_not_enqueued(db):
    """A stopped account must stop costing anything, including delivery attempts."""
    _user(db, active=True, status="suspended", telegram_status="connected")
    _posting(db)
    assert dispatch_due_notifications(db) == 0


def test_an_unscheduled_account_still_obeys_every_other_gate(db):
    """Reading "never run" as "due" must not become "due regardless of anything else"."""
    _user(db, active=True, status="suspended", telegram_status="connected")
    _user(db, active=True, status="active", telegram_status="broken")
    _user(db, active=False, status="active", telegram_status="connected")
    _posting(db)

    assert dispatch_due_notifications(db) == 0


def test_every_lane_has_a_claim_timeout():
    """A lane missing from the map crashes its worker on the first sweep, not on the first job."""
    from libs.db.models.queue import CLAIM_TIMEOUT_MIN, LANES

    assert set(LANES) == set(CLAIM_TIMEOUT_MIN), (
        "every lane needs a claim timeout and every timeout needs a lane")


def test_a_finished_job_records_when_it_actually_finished(db):
    """The finish is stamped when it happens, not when the claim did."""
    import time

    owner = _user(db, status="active")
    enqueue(db, "docgen", owner.id, payload={})

    job = claim(db, "docgen", worker="test")
    assert job is not None
    time.sleep(0.05)
    complete(db, job)

    stored = get(db, QueueJob, job.id)
    assert stored.state == "done"
    assert stored.finished_at > stored.claimed_at, "a job that took time must record that it took time"
