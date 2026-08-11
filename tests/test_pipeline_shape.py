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
    # No separate "store".
    assert keys == ["gather", "describe", "embed", "score", "deliver"]
    for entry in pipeline(db)["stages"]:
        assert entry["label"] and entry["what"], "every stage says what it is, on the page itself"


def test_a_row_waiting_to_be_embedded_is_counted_as_waiting(db):
    """The incident, in one assertion."""
    from libs.ops.pipeline import pipeline

    _job(db, description="We build things. " * 40, description_fetched_at=datetime.now(UTC))
    db.flush()

    assert stage(pipeline(db), "embed")["waiting"] == 1


def test_a_row_with_no_description_waits_at_describe_not_at_embed(db):
    """Each stage counts what *it* is holding."""
    from libs.ops.pipeline import pipeline

    _job(db)
    db.flush()

    report = pipeline(db)
    assert stage(report, "describe")["waiting"] == 1
    assert stage(report, "embed")["waiting"] == 0


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

    entry = stage(pipeline(db), "embed")
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


def test_scoring_counts_work_not_postings(db):
    """One corpus row waiting for three accounts is three units of work."""
    from libs.db.models import User
    from libs.ops.pipeline import pipeline

    for _ in range(2):
        user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active",
                    active=True, model_version=1)
        db.add(user)
    db.flush()
    _job(db, description="x " * 200, embedding=b"\x00" * 8, embedding_dim=2,
         embedding_version="v1", text_sha256=uuid.uuid4().hex * 2,
         posted_at=datetime.now(UTC))
    db.flush()

    assert stage(pipeline(db), "score")["waiting"] >= 2


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


def test_a_match_inside_its_own_sources_window_is_never_retired(db):
    """**The defect that destroyed 13 Adzuna matches two minutes after we first saw them.**

    Measured on the live platform 2026-08-11. `retire_stale_matches` read a flat
    `job_max_age_hours` — the operator had it at 6 — while the pre-score filter admits a posting
    against its own source floor, which is 48 hours for Adzuna and 168 for hiringcafe. So the filter
    let these in on purpose and the sweep destroyed them before the notifier could send them, with
    the sweep's own comment claiming it read "the same clock the pre-score filter reads".

    Every source except LinkedIn has a floor above six hours, so this reached every account.
    """
    from libs.tenant.filters import UserFilters
    from libs.tenant.repository import TenantRepository

    user = _account(db)
    # Eight hours old: past the operator's six, well inside Adzuna's forty-eight.
    admitted = _job(db, source="adzuna", description="x " * 200,
                    posted_at=datetime.now(UTC) - timedelta(hours=8))
    _scored(db, user, admitted, score=99.0)

    filters = UserFilters.from_config(None, max_age_hours=6)
    retired = TenantRepository(db).retire_stale_matches(user.id, cutoff=60.0, filters=filters)
    db.flush()

    assert retired == 0, (
        "a posting the filter admitted under its source's own floor was retired against the "
        "operator's global window — it can never be delivered, and nothing raises")


def test_a_match_past_its_own_sources_window_is_still_retired(db):
    """The other half: the sweep must still give up, or nothing ever expires."""
    from libs.tenant.filters import UserFilters
    from libs.tenant.repository import TenantRepository

    user = _account(db)
    # Adzuna's floor is 48 hours; this is past it however the operator's window is set.
    gone = _job(db, source="adzuna", description="x " * 200,
                posted_at=datetime.now(UTC) - timedelta(hours=60))
    _scored(db, user, gone, score=99.0)

    retired = TenantRepository(db).retire_stale_matches(
        user.id, cutoff=60.0, filters=UserFilters.from_config(None, max_age_hours=6))

    assert retired == 1


def test_the_age_of_a_queue_describes_a_row_that_is_in_it(db):
    """The count carried the account's filters and the age did not."""
    from libs.ops.pipeline import pipeline

    user = _account(db)
    stale = _job(db, description="x " * 200, embedding=b"\x00" * 8, embedding_dim=2,
                 embedding_version="v1", text_sha256=uuid.uuid4().hex * 2,
                 posted_at=datetime.now(UTC) - timedelta(days=6))
    _scored(db, user, stale, score=99.0, scored_ago_min=60 * 138)

    entry = stage(pipeline(db), "deliver")
    assert entry["waiting"] == 0, "a six-day-old posting is not a delivery candidate"
    assert entry["oldest_min"] is None, (
        "so it cannot be the age of the delivery queue either — the count and the age have to ask "
        "the same question")


def test_the_scoring_queue_can_actually_turn_red(db):
    """The one colour that cannot fire is the one nobody notices is missing."""
    from libs.ops.pipeline import pipeline

    _account(db)
    old = _job(db, description="x " * 200, embedding=b"\x00" * 8, embedding_dim=2,
               embedding_version="v1", text_sha256=uuid.uuid4().hex * 2,
               posted_at=datetime.now(UTC) - timedelta(hours=3))
    old.first_seen_at = datetime.now(UTC) - timedelta(hours=3)
    db.flush()

    entry = stage(pipeline(db), "score")
    assert entry["waiting"] == 1
    assert entry["oldest_min"] is not None and entry["oldest_min"] > 150
    assert entry["verdict"] == "down"


def test_a_delivery_queue_is_the_cap_working_but_an_expired_match_is_not(db):
    """Delivery is rate-capped on purpose."""
    from libs.ops.pipeline import pipeline

    user = _account(db)
    fresh = _job(db, description="x " * 200, embedding=b"\x00" * 8, embedding_dim=2,
                 embedding_version="v1", text_sha256=uuid.uuid4().hex * 2,
                 posted_at=datetime.now(UTC) - timedelta(minutes=10))
    _scored(db, user, fresh, score=99.0)

    queued = stage(pipeline(db), "deliver")
    assert queued["waiting"] == 1
    assert queued["expired"] == 0
    assert queued["verdict"] == "ok", "a queue behind a deliberate cap is not a fault"

    missed = _job(db, description="x " * 200, embedding=b"\x00" * 8, embedding_dim=2,
                  embedding_version="v1", text_sha256=uuid.uuid4().hex * 2,
                  posted_at=datetime.now(UTC) - timedelta(days=2))
    _scored(db, user, missed, score=99.0, scored_ago_min=30)

    # `expired` is what the sweep gave up on, not a condition re-derived here: a row counts once
    # somebody has actually decided it will never be sent.
    from libs.db.models import UserJobState
    from libs.tenant.repository import TenantRepository

    assert stage(pipeline(db), "deliver")["expired"] == 0, "nothing has given up on it yet"

    from libs.tenant.filters import UserFilters

    assert TenantRepository(db).retire_stale_matches(
        user.id, cutoff=60.0,
        filters=UserFilters.from_config(None, max_age_hours=24)) == 1
    db.flush()

    entry = stage(pipeline(db), "deliver")
    assert entry["expired"] == 1, "matched, never sent, and now written off"
    assert entry["verdict"] == "down"
    assert db.get(UserJobState, (user.id, missed.id)).notified_at is None, (
        "a row we failed to send in time is not a row we delivered")


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


def test_a_closed_posting_is_not_counted_as_waiting_to_be_embedded(db):
    """The page must not report work no worker will ever do.

    The embedder skips closed rows on purpose — the pre-score filter refuses them, so a vector for
    one is CPU spent on nothing. The dashboard asked only `trimmed_at IS NULL`, so two closed
    greenhouse postings sat in the Embedded stage for 25 hours reading as a stuck pipeline while
    the embedder was idle and correct.
    """
    from libs.corpus.repository import CorpusRepository
    from libs.ops.pipeline import pipeline

    _job(db, description="x" * 400, closed_at=datetime.now(UTC))
    db.flush()

    stages = {stage["key"]: stage for stage in pipeline(db, hours=24)["stages"]}
    pending = CorpusRepository(db).awaiting_embedding(limit=500)

    assert stages["embed"]["waiting"] == len(pending), (
        "the page and the embedder must count the same rows")


def test_an_open_posting_awaiting_a_vector_is_still_counted(db):
    """The rule is about closed rows, not about hiding a real backlog."""
    from libs.ops.pipeline import pipeline

    before = {s["key"]: s for s in pipeline(db, hours=24)["stages"]}["embed"]["waiting"]
    _job(db, description="x" * 400)
    db.flush()
    after = {s["key"]: s for s in pipeline(db, hours=24)["stages"]}["embed"]["waiting"]

    assert after == before + 1


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
