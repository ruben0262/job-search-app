"""What the latency page measures, and the two ways it has already been wrong."""
from __future__ import annotations

import uuid
from dataclasses import fields
from datetime import timedelta

import pytest

from libs.db import insert, now
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


def test_percentiles_interpolate_between_ranks_as_postgres_did():
    """The numbers on the page kept their meaning across the move: `percentile_cont`, not the nearest rank."""
    from libs.ops.latency import _measure

    measured = _measure([4.0, 1.0, 3.0, 2.0])
    assert measured["p50"] == 2.5, "the median of four is between the middle two"
    assert measured["p90"] == 3.7
    assert measured["worst"] == 4.0
    assert measured["samples"] == 4
    assert _measure([7.0])["p99"] == 7.0, "one example is its own every percentile"
    assert _measure([])["samples"] == 0


def test_thresholds_follow_from_how_the_pipeline_actually_runs():
    """Pinned to how the pipeline runs rather than to literals."""
    from libs.ops.latency import THRESHOLDS
    from libs.settings import PLATFORM_TICK_MIN

    amber, red = THRESHOLDS["found_to_delivered"]
    assert amber >= PLATFORM_TICK_MIN, (
        "a job found just after a send waits most of a harvest tick by design, so amber must sit "
        "above one tick rather than fire on it")
    assert red > amber
    # End to end is the one a user recognises, so it is the most forgiving of them.
    assert all(THRESHOLDS["found_to_delivered"][0] >= other[0] for other in THRESHOLDS.values())


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
        "found_to_delivered",
    ]
    # Each row hands its end timestamp to the next row's start, or there is a gap between them.
    crossings = [stage for stage in STAGES if stage.key != "found_to_delivered"]
    for earlier, later in pairwise(crossings):
        assert earlier.end == later.start, (
            f"{earlier.key} ends at {earlier.end} and {later.key} starts at {later.start}; "
            "the time in between belongs to no row")


def test_every_stage_reads_fields_its_documents_have():
    """A misspelt field is not an error in MongoDB: it is a stage with no samples, forever.

    And a description is fetched once for everybody, so shared work reads the posting alone; only
    delivery, which happens per account, ends on a person's own row.
    """
    from libs.ops.latency import STAGES

    posting = {field.name for field in fields(CorpusJob)}
    personal = {field.name for field in fields(UserJobState)}
    for stage in STAGES:
        assert stage.start in posting, f"{stage.key} starts at {stage.start}, which no posting has"
        if stage.scope == "corpus":
            assert stage.end in posting, f"{stage.key} is shared work but ends on {stage.end}"
        else:
            assert stage.scope == "user"
            assert stage.end in personal, f"{stage.key} ends at {stage.end}, which no account row has"


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


def test_an_unjudged_zone_is_not_the_same_answer_as_too_little_data(monkeypatch):
    """One means "we chose not to colour this, here is why"; the other means "not enough examples to say"."""
    from libs.ops import latency

    calls = {}

    def fake_spans(_db, stage, *, hours):
        calls[stage.key] = hours
        return [(now(), 2.0)] * 5_000

    monkeypatch.setattr(latency, "_spans", fake_spans)
    rows = {row["key"]: row for row in latency.stage_breakdown(object(), hours=6)}

    assert rows["posted_to_fetched"]["verdict"] == "unjudged"
    assert rows["posted_to_fetched"]["judged"] is False
    # A judged stage with plenty of samples and a fast p90 is simply healthy.
    assert rows["found_to_described"]["verdict"] == "ok"
    assert rows["found_to_described"]["judged"] is True
    # The window is the operator's choice and has to reach every stage, not just the first.
    assert set(calls) == {stage.key for stage in latency.STAGES}
    assert set(calls.values()) == {6}


def test_an_unknown_stage_key_never_reaches_the_database():
    """The key is a path segment, and a stage's query is built from the field names it names."""
    from libs.ops.latency import stage_detail

    with pytest.raises(KeyError):
        stage_detail(object(), "'; drop table corpus; --")


# ── the same measurements, against a real database ──────────────────────────────────────────────

def _posting(db, *, minutes_ago: float, described_after: float | None = None,
             fetched_before: float | None = None, posted_before: float | None = None,
             indexed_after: float | None = None):
    """One posting, aged, with its handoffs at known offsets from when it was found."""
    found = now() - timedelta(minutes=minutes_ago)

    def at(offset: float | None):
        return None if offset is None else found + timedelta(minutes=offset)

    job = insert(db, CorpusJob(
        url=f"https://example.test/{uuid.uuid4()}", source="test", title="Stress Engineer",
        first_seen_at=found,
        fetched_at=at(None if fetched_before is None else -fetched_before),
        posted_at=at(None if posted_before is None else -posted_before),
        description="x" * 500 if described_after is not None else None,
        description_fetched_at=at(described_after),
        # As `set_description` does: whole text, and when it came.
        described_at=at(described_after),
        terms_at=at(None if described_after is None or indexed_after is None
                    else described_after + indexed_after)))
    return job, found


def _delivered(db, job, at) -> None:
    user = insert(db, User(email=f"{uuid.uuid4()}@example.test", password_hash="x"))
    insert(db, UserJobState(user_id=user.id, job_id=job.id, notified_at=at))


def test_shared_work_is_counted_once_however_many_accounts_hold_the_posting(db):
    """A description is fetched once for everybody."""
    from libs.ops.latency import stage_breakdown

    job, found = _posting(db, minutes_ago=60, described_after=10)
    for _ in range(3):
        _delivered(db, job, found + timedelta(minutes=20))

    rows = {row["key"]: row for row in stage_breakdown(db, hours=24)}

    # One posting, one fetch -- regardless of the three accounts holding it.
    assert rows["found_to_described"]["samples"] == 1
    # Delivery genuinely happens per account, so three is the right answer there.
    assert rows["found_to_delivered"]["samples"] == 3


def test_each_zone_reports_its_own_minutes_rather_than_the_whole_journey(db):
    """The point of the breakdown: 40 minutes end to end is useless until it says *which* handoff ate them."""
    from libs.ops.latency import pipeline_latency, stage_breakdown

    job, found = _posting(db, minutes_ago=120, described_after=10, fetched_before=0.5, posted_before=30.5)
    _delivered(db, job, found + timedelta(minutes=40))

    rows = {row["key"]: row for row in stage_breakdown(db, hours=24)}

    assert rows["posted_to_fetched"]["p50"] == 30.0
    assert rows["fetched_to_stored"]["p50"] == 0.5
    assert rows["found_to_described"]["p50"] == 10.0
    # And the total is what the person waited, not the sum of anybody's percentiles.
    assert rows["found_to_delivered"]["p50"] == 40.0
    assert pipeline_latency(db, hours=24)["found_to_delivered"]["p50"] == 40.0



def test_the_drill_down_read_once_is_the_same_as_its_parts_read_apart(db):
    """`latency_report` reads each zone once; the route read found → delivered three times."""
    from libs.ops import latency

    job, found = _posting(db, minutes_ago=120, described_after=10, fetched_before=0.5, posted_before=30.5)
    _delivered(db, job, found + timedelta(minutes=40))
    _delivered(db, job, found + timedelta(minutes=70))

    report = latency.latency_report(db, hours=24)

    assert report["stages"] == latency.stage_breakdown(db, hours=24)
    assert report["spans"] == latency.pipeline_latency(db, hours=24)
    assert report["lanes"] == latency.lane_timings(db, hours=24)
    delivered = latency._spans(db, latency._BY_KEY["found_to_delivered"], hours=24)
    assert report["series"] == latency._hourly(delivered) and report["series"], "one hour, two deliveries"
    assert report["spans"]["found_to_delivered"]["samples"] == 2

def test_the_backlog_counts_what_has_not_got_through_rather_than_what_has(db):
    """The percentiles describe what already completed."""
    from libs.ops.latency import stage_detail

    _posting(db, minutes_ago=30, described_after=5)
    _posting(db, minutes_ago=45)

    describe = stage_detail(db, "found_to_described", hours=24)
    assert describe["backlog"]["waiting"] == 1
    assert describe["backlog"]["oldest_min"] >= 44
    assert len(describe["series"]) == 1, "the one that did get through is still measured"


def test_a_backfill_never_reports_itself_as_pipeline_latency(db):
    """A posting found days ago and touched just now is old work, not a slow pipeline."""
    from libs.ops.latency import stage_breakdown

    nine_days = 60 * 24 * 9
    # Described just now, and delivered just now, against a posting found nine days ago.
    old, _found = _posting(db, minutes_ago=nine_days, described_after=nine_days - 1)
    _delivered(db, old, now())

    rows = {row["key"]: row for row in stage_breakdown(db, hours=24)}
    assert rows["found_to_described"]["samples"] == 0
    assert rows["found_to_delivered"]["samples"] == 0


def test_both_admin_views_read_the_same_backlogs(db):
    """Pipeline's waiting and Latency's backlog are one function's answer, for the step with a queue."""
    from libs.ops.latency import stage_detail
    from libs.ops.pipeline import pipeline

    _posting(db, minutes_ago=30)                                       # its text, and its title's words
    _posting(db, minutes_ago=50, described_after=20)                   # its words
    _posting(db, minutes_ago=70, described_after=10, indexed_after=5)  # nothing

    shown = {row["key"]: row for row in pipeline(db)["stages"]}
    for step, key, waits in (("describe", "found_to_described", 1),):
        backlog = stage_detail(db, key)["backlog"]
        assert backlog["waiting"] == shown[step]["waiting"] == waits
        assert backlog["oldest_min"] == pytest.approx(shown[step]["oldest_min"], abs=0.2)
