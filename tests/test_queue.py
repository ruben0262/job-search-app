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


def test_a_job_that_always_kills_its_worker_dies_and_is_not_claimed_for_ever(db):
    """The test above says so and nothing did it: a job whose worker stops under it never reaches `fail`,
    where attempts are counted, so its expired claim went back to ready however many it had had. On the
    docgen lane each of those claims writes a document on the person's own AI plan (2026-11-20)."""
    from libs.db.models import MAX_ATTEMPTS

    user = _user(db)
    enqueue(db, "docgen", user.id, payload={"job_id": 1})
    for attempt in range(1, MAX_ATTEMPTS + 1):
        job = claim(db, "docgen", f"worker-{attempt}")
        assert job is not None and job.attempts == attempt
        _age(db, job.id, claimed_at=timedelta(hours=1))
        sweep_expired(db, "docgen")

    assert claim(db, "docgen", "one-more") is None
    dead = get(db, QueueJob, job.id)
    assert dead.state == "dead" and "stopped under it" in dead.last_error


def test_a_live_claim_is_not_swept(db):
    """The sweep returns only what has outlived its lane's timeout, or two workers run one job."""
    user = _user(db)
    enqueue(db, "notify", user.id)
    job = claim(db, "notify", "worker-a")
    _age(db, job.id, claimed_at=timedelta(minutes=1))

    assert sweep_expired(db, "notify") == 0
    assert get(db, QueueJob, job.id).state == "claimed"


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


# ── many at once ──────────────────────────────────────────────────────────────────────────────

def test_enqueue_many_adds_one_each_and_leaves_what_is_pending(db):
    """The alerts queue everybody they caught a job for at once (`libs.alerts`): one write, and the same
    work already waiting for somebody is not added twice."""
    from libs.queue.queue import enqueue_many

    waiting, fresh, other = _user(db), _user(db), _user(db)
    enqueue(db, "notify", waiting.id, dedup_key="due")

    added = enqueue_many(db, "notify", [waiting.id, fresh.id, other.id], dedup_key="due")

    assert len(added) == 2
    assert {get(db, QueueJob, job_id).user_id for job_id in added} == {fresh.id, other.id}
    assert db[QueueJob.COLLECTION].count_documents({"lane": "notify", "state": "ready"}) == 3
    assert enqueue_many(db, "notify", [fresh.id], dedup_key="due") == [], "one waiting is enough"


def test_enqueue_many_adds_nothing_to_a_paused_lane(db):
    from libs.db.models import LanePause
    from libs.queue.queue import enqueue_many

    insert(db, LanePause(id="notify"))
    assert enqueue_many(db, "notify", [_user(db).id], dedup_key="due") == []
    assert db[QueueJob.COLLECTION].count_documents({}) == 0


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
