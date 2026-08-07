"""What the latency page measures, and the two ways it has already been wrong."""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from libs.db.models import CorpusJob, User, UserJobState


def test_a_verdict_is_judged_on_the_tail_not_the_median():
    """Half the jobs arriving promptly is not the claim this product makes."""
    from libs.ops.latency import verdict

    flattering = {"p50": 2.0, "p90": 200.0, "p99": 400.0, "samples": 500}
    assert verdict("found_to_delivered", flattering) == "down"


def test_too_few_samples_is_unknown_rather_than_a_colour():
    """Below the floor a percentile is arithmetic rather than evidence."""
    from libs.ops.latency import MIN_SAMPLES, verdict

    assert verdict("found_to_delivered", {"p90": 999.0, "samples": MIN_SAMPLES - 1}) == "unknown"
    assert verdict("found_to_delivered", {"p90": 999.0, "samples": MIN_SAMPLES}) == "down"
    # No completed examples at all is not a failing pipeline either.
    assert verdict("found_to_delivered", {"p90": None, "samples": 0}) == "unknown"


def test_thresholds_follow_from_how_the_pipeline_actually_runs():
    """Pinned to how the pipeline runs rather than to literals."""
    from libs.ops.latency import THRESHOLDS
    from libs.settings import PLATFORM_TICK_MIN

    amber, red = THRESHOLDS["scored_to_delivered"]
    assert amber >= PLATFORM_TICK_MIN, (
        "delivery is event-driven now, so this span should be minutes — but a backlog draining at "
        "the send pace is healthy, and amber must sit above that rather than fire on it")
    assert red > amber
    # End to end is the one a user recognises, so it is the most forgiving of the three.
    assert THRESHOLDS["found_to_delivered"][0] >= THRESHOLDS["found_to_scored"][0]


def test_the_worst_span_is_the_pipeline_verdict():
    """One word for "is this healthy", derived from the rows rather than alongside them."""
    from libs.ops.latency import _worst

    assert _worst(["ok", "ok", "ok"]) == "ok"
    assert _worst(["ok", "warn", "ok"]) == "warn"
    assert _worst(["ok", "warn", "down"]) == "down"
    # `unknown` never outranks a real verdict.
    assert _worst(["unknown", "down"]) == "down"
    assert _worst(["unknown", "unknown"]) == "unknown"


# ── the drill-down: one row per zone ─────────────────────────────────────────────────────────────

def test_every_zone_the_posting_crosses_is_a_row_and_they_are_in_order():
    """The rows are the journey, and a gap in them is a gap nobody can see."""
    from itertools import pairwise

    from libs.ops.latency import STAGES

    assert [stage.key for stage in STAGES] == [
        "posted_to_fetched",
        "fetched_to_stored",
        "found_to_described",
        "described_to_embedded",
        "embedded_to_scored",
        "scored_to_delivered",
        "found_to_delivered",
    ]
    # Each row hands its end timestamp to the next row's start, or there is a gap between them.
    crossings = [stage for stage in STAGES if stage.key != "found_to_delivered"]
    for earlier, later in pairwise(crossings):
        assert earlier.end == later.start, (
            f"{earlier.key} ends at {earlier.end} and {later.key} starts at {later.start}; "
            "the time in between belongs to no row")


def test_a_shared_stage_is_never_measured_through_the_per_user_join():
    """A description is fetched once for everybody."""
    from libs.ops.latency import _SCOPE_FROM, STAGES

    for stage in STAGES:
        assert stage.scope in _SCOPE_FROM
        if stage.scope == "corpus":
            assert "s." not in f"{stage.start}{stage.end}", (
                f"{stage.key} is shared work but reads a per-user column")
    assert "user_job_state" not in _SCOPE_FROM["corpus"]


def test_harvest_lag_is_reported_and_deliberately_not_judged():
    """Sources round what they publish -- LinkedIn says "2 days ago"."""
    from libs.ops.latency import STAGES, THRESHOLD_REASONING, THRESHOLDS

    assert "posted_to_fetched" not in THRESHOLDS
    # Unjudged is a decision, so it owes the reader the reason, in the same place the others do.
    assert THRESHOLD_REASONING["posted_to_fetched"]
    lag = next(stage for stage in STAGES if stage.key == "posted_to_fetched")
    assert lag.caveat

    # And the half that *is* ours is judged, tightly — that is the whole point of the split.
    assert THRESHOLDS["fetched_to_stored"][0] <= 5


def test_every_judged_zone_says_why_its_threshold_is_where_it_is():
    """A colour whose reasoning lives only in a Python constant is a colour nobody trusts."""
    from libs.ops.latency import STAGES, THRESHOLD_REASONING, THRESHOLDS

    for stage in STAGES:
        assert stage.key in THRESHOLD_REASONING, f"{stage.key} has no stated reasoning"
        if stage.key in THRESHOLDS:
            amber, red = THRESHOLDS[stage.key]
            assert red > amber


def test_the_embed_zone_is_judged_tighter_than_the_ones_waiting_on_a_cadence():
    """The embedder drains continuously and answers in milliseconds."""
    from libs.ops.latency import THRESHOLDS

    assert THRESHOLDS["described_to_embedded"][0] < THRESHOLDS["found_to_described"][0]


def test_an_unjudged_zone_is_not_the_same_answer_as_too_little_data(monkeypatch):
    """One means "we chose not to colour this, here is why"; the other means "not enough examples to say"."""
    from libs.ops import latency

    calls = {}

    def fake_span(_session, stage, *, hours):
        calls[stage.key] = hours
        return {"p50": 1.0, "p90": 2.0, "p99": 3.0, "worst": 4.0, "samples": 5_000}

    monkeypatch.setattr(latency, "_stage_span", fake_span)
    rows = {row["key"]: row for row in latency.stage_breakdown(object(), hours=6)}

    assert rows["posted_to_fetched"]["verdict"] == "unjudged"
    assert rows["posted_to_fetched"]["judged"] is False
    # A judged stage with plenty of samples and a fast p90 is simply healthy.
    assert rows["found_to_described"]["verdict"] == "ok"
    assert rows["found_to_described"]["judged"] is True
    # The window is the operator's choice and has to reach every stage, not just the first.
    assert set(calls.values()) == {6}


def test_an_unknown_stage_key_never_reaches_sql():
    """The key is a path segment, and the SQL for a stage is built by formatting column identifiers into."""
    from libs.ops.latency import stage_detail

    with pytest.raises(KeyError):
        stage_detail(object(), "'; drop table corpus; --")


# ── the same measurements, against a real Postgres ──────────────────────────────────────────────

pytestmark_db = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (no live Postgres)")


@pytest.fixture()
def ctx():
    """A rolled-back transaction. Committing would leave corpus rows other tests would measure."""
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


def _posting(s, *, minutes_ago: float, described_after: float | None = None,
             embedded_after: float | None = None):
    """One corpus row, aged, with its handoffs at known offsets from when it was found."""
    found = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    job = CorpusJob(
        url=f"https://example.test/{uuid.uuid4()}",
        source="test", title="Stress Engineer",
        first_seen_at=found, last_seen_at=found,
        description="x" * 500 if described_after is not None else None,
        description_fetched_at=(found + timedelta(minutes=described_after)
                                if described_after is not None else None),
        embedded_at=(found + timedelta(minutes=embedded_after)
                     if embedded_after is not None else None),
    )
    s.add(job)
    s.flush()
    return job, found


@pytestmark_db
def test_shared_work_is_counted_once_however_many_accounts_hold_the_posting(ctx):
    """A description is fetched once for everybody."""
    from libs.ops.latency import stage_breakdown

    job, _found = _posting(ctx, minutes_ago=60, described_after=10, embedded_after=12)
    for _ in range(3):
        user = User(email=f"{uuid.uuid4()}@example.test", password_hash="x")
        ctx.add(user)
        ctx.flush()
        ctx.add(UserJobState(user_id=user.id, job_id=job.id, score=70.0,
                             scored_at=job.embedded_at + timedelta(minutes=3),
                             notified_at=job.embedded_at + timedelta(minutes=8)))
    ctx.flush()

    rows = {row["key"]: row for row in stage_breakdown(ctx, hours=24)}

    # One posting, one fetch, one embed -- regardless of the three accounts holding it.
    assert rows["found_to_described"]["samples"] == 1
    assert rows["described_to_embedded"]["samples"] == 1
    # Scoring and delivery genuinely happen per account, so three is the right answer there.
    assert rows["embedded_to_scored"]["samples"] == 3
    assert rows["scored_to_delivered"]["samples"] == 3


@pytestmark_db
def test_each_zone_reports_its_own_minutes_rather_than_the_whole_journey(ctx):
    """The point of the breakdown: 36 minutes end to end is useless until it says *which* handoff ate them."""
    from libs.ops.latency import stage_breakdown

    job, found = _posting(ctx, minutes_ago=120, described_after=10, embedded_after=25)
    user = User(email=f"{uuid.uuid4()}@example.test", password_hash="x")
    ctx.add(user)
    ctx.flush()
    ctx.add(UserJobState(user_id=user.id, job_id=job.id, score=70.0,
                         scored_at=found + timedelta(minutes=30),
                         notified_at=found + timedelta(minutes=40)))
    ctx.flush()

    rows = {row["key"]: row for row in stage_breakdown(ctx, hours=24)}

    assert rows["found_to_described"]["p50"] == 10.0
    assert rows["described_to_embedded"]["p50"] == 15.0  # 25 - 10
    assert rows["embedded_to_scored"]["p50"] == 5.0      # 30 - 25
    assert rows["scored_to_delivered"]["p50"] == 10.0    # 40 - 30
    # And the total is what the person waited, not the sum of anybody's percentiles.
    assert rows["found_to_delivered"]["p50"] == 40.0


@pytestmark_db
def test_the_backlog_counts_what_has_not_got_through_rather_than_what_has(ctx):
    """The percentiles describe what already completed."""
    from libs.ops.latency import stage_detail

    described, _ = _posting(ctx, minutes_ago=30, described_after=5)
    _waiting, _ = _posting(ctx, minutes_ago=45)
    ctx.flush()

    describe = stage_detail(ctx, "found_to_described", hours=24)
    assert describe["backlog"]["waiting"] >= 1
    assert describe["backlog"]["oldest_min"] >= 44

    # The row with text and no vector is the embedder's queue, not the describe pass's.
    embed = stage_detail(ctx, "described_to_embedded", hours=24)
    assert embed["backlog"]["waiting"] >= 1
    assert described.embedding is None


@pytestmark_db
def test_a_backfill_never_reports_itself_as_pipeline_latency(ctx):
    """A rescore of the whole corpus happens on every retrain."""
    from libs.ops.latency import stage_breakdown

    old, _found = _posting(ctx, minutes_ago=60 * 24 * 9, described_after=10, embedded_after=12)
    user = User(email=f"{uuid.uuid4()}@example.test", password_hash="x")
    ctx.add(user)
    ctx.flush()
    # Scored and delivered just now, against a posting found nine days ago.
    ctx.add(UserJobState(user_id=user.id, job_id=old.id, score=70.0,
                         scored_at=datetime.now(UTC) - timedelta(minutes=5),
                         notified_at=datetime.now(UTC)))
    ctx.flush()

    rows = {row["key"]: row for row in stage_breakdown(ctx, hours=24)}
    assert rows["found_to_delivered"]["samples"] == 0
    assert rows["embedded_to_scored"]["samples"] == 0


# ── what the filter removed ─────────────────────────────────────────────────────────────────────


def _judged(ctx, *, scored: int, skipped: int):
    """One account with a known split of scored and filtered-out rows."""
    from libs.ops.latency import filter_effect

    user = User(email=f"{uuid.uuid4()}@example.test", password_hash="x")
    ctx.add(user)
    ctx.flush()
    now = datetime.now(UTC)
    for index in range(scored + skipped):
        job, _ = _posting(ctx, minutes_ago=30, described_after=1, embedded_after=2)
        ctx.add(UserJobState(user_id=user.id, job_id=job.id,
                             # `skip()` stores a null score with `scored_at` set.
                             score=None if index < skipped else 70.0,
                             scored_model_version=1, scored_at=now))
    ctx.flush()
    return user, {entry["user_id"]: entry
                  for entry in filter_effect(ctx, hours=24)["users"]}[str(user.id)]


@pytestmark_db
def test_a_filtered_row_is_counted_as_removed_not_as_missing(ctx):
    """The before/after count the filter shipped without (CLAUDE.md §4)."""
    _user, entry = _judged(ctx, scored=7, skipped=3)

    assert entry["considered"] == 10
    assert entry["removed"] == 3
    assert entry["scored"] == 7
    assert entry["removed_pct"] == 30.0


@pytestmark_db
def test_one_account_whose_filter_removes_everything_is_visible_on_its_own(ctx):
    """A total would hide it, and the total is what an operator reads first."""
    from libs.ops.latency import MIN_SAMPLES, filter_effect

    _healthy, healthy = _judged(ctx, scored=MIN_SAMPLES, skipped=0)
    _silent, silent = _judged(ctx, scored=0, skipped=MIN_SAMPLES)

    assert silent["verdict"] == "down"
    assert silent["removed_pct"] == 100.0
    assert healthy["verdict"] == "ok", "removing nothing is cold start, not a fault"
    # And the platform summary carries the worst of them rather than the average.
    assert filter_effect(ctx, hours=24)["verdict"] == "down"


@pytestmark_db
def test_a_row_nobody_has_scored_is_not_counted_as_filtered_out(ctx):
    """`user_job_state` gains a row the first time anything touches a posting — a label, an applied record."""
    from libs.ops.latency import filter_effect

    user = User(email=f"{uuid.uuid4()}@example.test", password_hash="x")
    ctx.add(user)
    ctx.flush()
    job, _ = _posting(ctx, minutes_ago=30, described_after=1, embedded_after=2)
    ctx.add(UserJobState(user_id=user.id, job_id=job.id, applied_at=datetime.now(UTC)))
    ctx.flush()

    assert not [entry for entry in filter_effect(ctx, hours=24)["users"]
                if entry["user_id"] == str(user.id)]
