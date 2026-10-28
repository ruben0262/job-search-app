"""Where the jobs are — and whether the page telling you can be trusted."""
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


def _job(session, **kwargs):
    from libs.db.models import CorpusJob

    # `source` is overridable: the per-source freshness floors are keyed on it, so a test about
    # retention has to be able to say which board a posting came from.
    row = CorpusJob(url=f"https://example.com/{uuid.uuid4()}",
                    source=kwargs.pop("source", "test"),
                    title="Backend Engineer", **kwargs)
    session.add(row)
    session.flush()
    return row


def stage(report: dict, key: str) -> dict:
    return next(s for s in report["stages"] if s["key"] == key)


def test_every_stage_of_the_pipeline_is_present(db):
    """Missing stages are the failure this exists to prevent."""
    from libs.ops.pipeline import pipeline

    keys = [s["key"] for s in pipeline(db)["stages"]]
    # No separate "store", and no "embed" or "score": nothing embeds or scores (ROADMAP Phase 8).
    assert keys == ["gather", "describe", "deliver"]
    for entry in pipeline(db)["stages"]:
        assert entry["label"] and entry["what"], "every stage says what it is, on the page itself"


def test_a_described_posting_is_waiting_for_nothing(db):
    """It is matched when it is read. A vector nothing computes is not a backlog to show anybody."""
    from libs.ops.pipeline import pipeline

    _job(db, description="We build things. " * 40, description_fetched_at=datetime.now(UTC))
    db.flush()

    assert all(not entry["waiting"] for entry in pipeline(db)["stages"])


def test_a_row_with_no_description_waits_at_describe(db):
    """Each stage counts what *it* is holding."""
    from libs.ops.pipeline import pipeline

    _job(db)
    db.flush()

    assert stage(pipeline(db), "describe")["waiting"] == 1


# A trimmed row used to be the case here: retention nulled a description in place, leaving a row
# that read as waiting to be described forever. Retention deletes the row now, so there is no such
# state to exclude — the backlog cannot count what is not there. The closed-posting case below is
# the one that still needs asserting, because a closed row does stay in the corpus.


def test_the_verdict_is_about_age_not_size(db):
    """A thousand rows moving is fine; forty stuck for a day is not."""
    from libs.ops.pipeline import pipeline

    for _ in range(50):
        _job(db)
    db.flush()

    assert stage(pipeline(db), "describe")["verdict"] == "ok", "fresh, however many"


def test_a_stage_holding_something_old_turns_red(db):
    """The other half: one row nobody has picked up for hours is the signal.

    Three hours rather than a day, because a day-old posting is past the delivery window and the
    describer no longer fetches it — see the test below. Red now means "inside the window and
    nobody has taken it", which is the condition an operator can actually act on.
    """
    from libs.ops.pipeline import pipeline

    old = _job(db)
    old.first_seen_at = datetime.now(UTC) - timedelta(hours=3)
    db.flush()

    entry = stage(pipeline(db), "describe")
    assert entry["verdict"] == "down"
    assert entry["oldest_min"] > 60


def test_a_posting_too_old_to_deliver_is_not_reported_as_waiting(db):
    """It is not waiting for anything. Nothing will ever fetch it and nobody could receive it.

    Measured in production on 2026-08-08: the describer was retrying 212 thin LinkedIn rows every
    fifteen minutes, 156 of them already past the delivery window, the oldest first seen 167 hours
    earlier — the same ids, on a user's own metered proxy, for a week.
    """
    from libs.corpus.repository import CorpusRepository
    from libs.ops.pipeline import pipeline

    stale = _job(db, description=None)
    stale.first_seen_at = datetime.now(UTC) - timedelta(days=3)
    db.flush()

    assert stage(pipeline(db), "describe")["waiting"] == 0
    assert not [row for row in CorpusRepository(db).awaiting_description(max_age_hours=24)
                if row.id == stale.id], "and the describer agrees"


def test_an_empty_stage_is_green_and_says_nothing_about_age(db):
    """`None` rather than zero. Zero minutes is a claim about a queue that does not exist."""
    from libs.ops.pipeline import pipeline

    entry = stage(pipeline(db), "describe")
    assert entry["waiting"] == 0
    assert entry["oldest_min"] is None
    assert entry["verdict"] == "ok"


def test_the_thresholds_come_from_the_tick_that_clears_them(db):
    """Derived, not chosen — the harvest interval is what is supposed to drain these stages."""
    from libs.ops.pipeline import pipeline
    from libs.settings import PLATFORM_TICK_MIN

    report = pipeline(db)
    assert report["amber_after_min"] == 2 * PLATFORM_TICK_MIN
    assert report["red_after_min"] == 8 * PLATFORM_TICK_MIN


# ── the three ways the first version lied ───────────────────────────────────────────────────────


def _account(db, **config):
    from libs.db.models import User, UserConfig

    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active",
                active=True, model_version=1)
    db.add(user)
    db.flush()
    if config:
        db.add(UserConfig(user_id=user.id, config=config))
        db.flush()
    return user


def _scored(db, user, job, *, score, notified=False, scored_ago_min=5):
    from libs.db.models import UserJobState

    db.add(UserJobState(
        user_id=user.id, job_id=job.id, score=score, scored_model_version=user.model_version,
        scored_at=datetime.now(UTC) - timedelta(minutes=scored_ago_min),
        notified_at=datetime.now(UTC) if notified else None))
    db.flush()


def test_delivery_shows_no_queue_for_scores_nothing_will_send(db):
    """The scorer's rows outlive it, and nothing sends them, so they are not a queue at delivery."""
    from libs.ops.pipeline import pipeline

    user = _account(db)
    _scored(db, user, _job(db, description="x " * 200,
                           posted_at=datetime.now(UTC) - timedelta(minutes=10)), score=99.0)
    _scored(db, user, _job(db, description="x " * 200), score=99.0, notified=True)

    entry = stage(pipeline(db), "deliver")
    assert entry["waiting"] is None
    assert entry["verdict"] == "ok"
    assert entry["entered"] >= 1, "what was sent in the window is still counted"


def test_a_stage_with_no_queue_says_so_rather_than_claiming_zero(db):
    """Gathering and storing are one `INSERT`, so nothing is ever between them."""
    from libs.ops.pipeline import pipeline

    entry = stage(pipeline(db), "gather")
    assert entry["waiting"] is None
    assert entry["verdict"] == "ok"


def test_a_job_a_worker_is_running_is_counted_as_running(db):
    """The queue has no state called `running`."""
    from libs.db.models import QueueJob
    from libs.ops.pipeline import pipeline

    user = _account(db)
    db.add(QueueJob(lane="notify", user_id=user.id, state="claimed",
                    claimed_at=datetime.now(UTC), claimed_by="worker-1"))
    db.flush()

    notify = next(lane for lane in pipeline(db)["lanes"] if lane["lane"] == "notify")
    assert notify["running"] == 1


def test_the_page_and_the_describer_count_the_same_rows(db):
    """Same defect, same shape: a closed row is never fetched and must not read as waiting."""
    from libs.corpus.repository import CorpusRepository
    from libs.ops.pipeline import pipeline

    _job(db, description=None, closed_at=datetime.now(UTC))
    db.flush()

    stages = {stage["key"]: stage for stage in pipeline(db, hours=24)["stages"]}
    pending = CorpusRepository(db).awaiting_description(limit=1000)

    assert stages["describe"]["waiting"] == len(pending)


def test_the_queue_says_which_account_each_lane_is_working_for(db):
    """"docgen 2 dead" is not actionable until you know whose two.

    A lane is per-account work. The chips summed across accounts, so an operator seeing dead jobs
    had to open psql against production to find the owner — which is how an account that could not
    build a CV went unnoticed for three days.
    """
    import uuid as _uuid

    from libs.db.models import QueueJob, User
    from libs.ops.pipeline import pipeline

    first = User(email=f"{_uuid.uuid4()}@example.com", password_hash="x", status="active")
    second = User(email=f"{_uuid.uuid4()}@example.com", password_hash="x", status="active")
    db.add_all([first, second])
    db.flush()
    db.add_all([
        QueueJob(lane="docgen", user_id=first.id, state="dead",
                 last_error="AttributeError: 'NoneType' object has no attribute 'calls'"),
        QueueJob(lane="docgen", user_id=second.id, state="ready"),
    ])
    db.flush()

    rows = {(row["lane"], row["email"]): row for row in pipeline(db, hours=24)["lane_accounts"]}

    assert rows[("docgen", first.email)]["dead"] == 1
    assert "AttributeError" in rows[("docgen", first.email)]["last_error"]
    assert rows[("docgen", second.email)]["ready"] == 1
    assert rows[("docgen", second.email)]["dead"] == 0, "the two accounts are not conflated"
