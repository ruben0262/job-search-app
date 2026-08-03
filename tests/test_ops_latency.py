"""What the latency page measures, and the two ways it has already been wrong.

The pure tests pin the stage table's shape and the judgements over it; the ones at the bottom run
the statements against a real Postgres, because **a percentile over the wrong join is a number that
looks entirely plausible and describes a population that never existed** — which is the failure
mode this file exists for, not a crash.
"""
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
    """**Half the jobs arriving promptly is not the claim this product makes.**

    A median hides exactly the tail somebody loses a role in, so the verdict reads p90. A stage
    whose median is excellent and whose p90 is an hour is a stage that is failing the people it
    matters most to.
    """
    from libs.ops.latency import verdict

    flattering = {"p50": 2.0, "p90": 200.0, "p99": 400.0, "samples": 500}
    assert verdict("found_to_delivered", flattering) == "down"


def test_too_few_samples_is_unknown_rather_than_a_colour():
    """Below the floor a percentile is arithmetic rather than evidence. Colouring it invites
    somebody to chase a number that three rows produced, and an operator who chases two of those
    stops reading the column."""
    from libs.ops.latency import MIN_SAMPLES, verdict

    assert verdict("found_to_delivered", {"p90": 999.0, "samples": MIN_SAMPLES - 1}) == "unknown"
    assert verdict("found_to_delivered", {"p90": 999.0, "samples": MIN_SAMPLES}) == "down"
    # No completed examples at all is not a failing pipeline either.
    assert verdict("found_to_delivered", {"p90": None, "samples": 0}) == "unknown"


def test_thresholds_follow_from_how_the_pipeline_actually_runs():
    """Pinned to the cadence they were derived from rather than to literals.

    If delivery cadence changes, these thresholds are wrong and this test says so — which is the
    only thing standing between "amber means look at this" and "amber means nothing".
    """
    from libs.ops.latency import THRESHOLDS
    from libs.settings import DELIVERY_INTERVAL_MIN

    amber, red = THRESHOLDS["scored_to_delivered"]
    assert amber >= DELIVERY_INTERVAL_MIN, (
        "a job scored just after a send waits most of a cycle by design, so amber must sit above "
        "one full cadence or it fires on healthy behaviour")
    assert red > amber
    # End to end is the one a user recognises, so it is the most forgiving of the three.
    assert THRESHOLDS["found_to_delivered"][0] >= THRESHOLDS["found_to_scored"][0]


def test_the_worst_span_is_the_pipeline_verdict():
    """One word for "is this healthy", derived from the rows rather than alongside them -- a summary
    that can disagree with the table under it is worse than no summary."""
    from libs.ops.latency import _worst

    assert _worst(["ok", "ok", "ok"]) == "ok"
    assert _worst(["ok", "warn", "ok"]) == "warn"
    assert _worst(["ok", "warn", "down"]) == "down"
    # `unknown` never outranks a real verdict: one thin sample must not grey out a pipeline that
    # another span says is on fire.
    assert _worst(["unknown", "down"]) == "down"
    assert _worst(["unknown", "unknown"]) == "unknown"


# ── the drill-down: one row per zone ─────────────────────────────────────────────────────────────

def test_every_zone_the_posting_crosses_is_a_row_and_they_are_in_order():
    """**The rows are the journey, and a gap in them is a gap nobody can see.**

    A posting arrives as a link and leaves as a card, and each stage below is one boundary in
    between. If a boundary has no row, the time it eats is silently attributed to its neighbour --
    which is exactly what happened while embed had no timestamp, and why found -> scored could not
    say whether the embedder or the score lane was slow.
    """
    from itertools import pairwise

    from libs.ops.latency import STAGES

    assert [stage.key for stage in STAGES] == [
        "posted_to_found",
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
    """A description is fetched **once for everybody**, so counting it per account would multiply
    every posting by the number of users holding it and report a sample count that never happened.
    Only the stages that genuinely happen per account may join `user_job_state`."""
    from libs.ops.latency import _SCOPE_FROM, STAGES

    for stage in STAGES:
        assert stage.scope in _SCOPE_FROM
        if stage.scope == "corpus":
            assert "s." not in f"{stage.start}{stage.end}", (
                f"{stage.key} is shared work but reads a per-user column")
    assert "user_job_state" not in _SCOPE_FROM["corpus"]


def test_harvest_lag_is_reported_and_deliberately_not_judged():
    """**Sources round what they publish** -- LinkedIn says "2 days ago" -- so a threshold on this
    would be judging their precision rather than our lag, and a red pill produced by rounding is the
    exact colour an operator learns to ignore. It stays on the page because harvest lag is part of
    what a user waits, and dropping it would make the judged rows look like the whole story."""
    from libs.ops.latency import STAGES, THRESHOLD_REASONING, THRESHOLDS

    assert "posted_to_found" not in THRESHOLDS
    # Unjudged is a decision, so it owes the reader the reason, in the same place the others do.
    assert THRESHOLD_REASONING["posted_to_found"]
    lag = next(stage for stage in STAGES if stage.key == "posted_to_found")
    assert lag.caveat


def test_every_judged_zone_says_why_its_threshold_is_where_it_is():
    """A colour whose reasoning lives only in a Python constant is a colour nobody trusts, and this
    project has already had one number turn out to be measuring something other than its label."""
    from libs.ops.latency import STAGES, THRESHOLD_REASONING, THRESHOLDS

    for stage in STAGES:
        assert stage.key in THRESHOLD_REASONING, f"{stage.key} has no stated reasoning"
        if stage.key in THRESHOLDS:
            amber, red = THRESHOLDS[stage.key]
            assert red > amber


def test_the_embed_zone_is_judged_tighter_than_the_ones_waiting_on_a_cadence():
    """The embedder drains continuously and answers in milliseconds -- there is no schedule to wait
    for -- so minutes there mean a queue, where minutes after a harvest mean the next cycle. Judging
    them on the same threshold would forgive a stalled embedder for a quarter of an hour."""
    from libs.ops.latency import THRESHOLDS

    assert THRESHOLDS["described_to_embedded"][0] < THRESHOLDS["found_to_described"][0]


def test_an_unjudged_zone_is_not_the_same_answer_as_too_little_data(monkeypatch):
    """One means "we chose not to colour this, here is why"; the other means "not enough examples to
    say". Collapsing them makes a deliberate decision look like missing data, and an operator who
    reads a deliberate silence as a gap goes looking for a bug that is not there."""
    from libs.ops import latency

    calls = {}

    def fake_span(_session, stage, *, hours):
        calls[stage.key] = hours
        return {"p50": 1.0, "p90": 2.0, "p99": 3.0, "worst": 4.0, "samples": 5_000}

    monkeypatch.setattr(latency, "_stage_span", fake_span)
    rows = {row["key"]: row for row in latency.stage_breakdown(object(), hours=6)}

    assert rows["posted_to_found"]["verdict"] == "unjudged"
    assert rows["posted_to_found"]["judged"] is False
    # A judged stage with plenty of samples and a fast p90 is simply healthy.
    assert rows["found_to_described"]["verdict"] == "ok"
    assert rows["found_to_described"]["judged"] is True
    # The window is the operator's choice and has to reach every stage, not just the first.
    assert set(calls.values()) == {6}


def test_an_unknown_stage_key_never_reaches_sql():
    """The key is a path segment, and the SQL for a stage is built by formatting column identifiers
    into a template. Matching against the known stages first is what keeps that safe -- an
    identifier cannot be a bound parameter, so nothing else could."""
    from libs.ops.latency import stage_detail

    with pytest.raises(KeyError):
        stage_detail(object(), "'; drop table corpus; --")


# ── the same measurements, against a real Postgres ──────────────────────────────────────────────
#
# The tests above pin the stage table's shape; only these prove the statements run and count the
# right rows. Both matter: a percentile over the wrong join is a number that looks entirely
# plausible and describes something that never happened.

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
    """**A description is fetched once for everybody.**

    Counting it through the per-user join would multiply one fetch by the number of accounts that
    hold the row and report a sample count that never happened -- and the percentiles would be a
    real measurement of a fictional population, which is worse than a missing number because it
    looks right.
    """
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
    """The point of the breakdown: 36 minutes end to end is useless until it says *which* handoff
    ate them. Each row must measure its own boundary and nothing either side of it."""
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
    """**The percentiles describe what already completed.** A stage with a fine p90 and four
    thousand rows queued behind it is about to stop having a fine p90, and only this number says so
    before it happens."""
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
    """A rescore of the whole corpus happens on every retrain, and without the recency guard it
    reports the age of every old row as how fast a new job reaches somebody -- which is how this
    number once came out as a median of 3,759 minutes describing nothing that happened today."""
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
