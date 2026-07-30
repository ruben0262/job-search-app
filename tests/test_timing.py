"""Stage timing, and the one property that makes it worth having.

Timing code is easy to write and easy to write uselessly. The tests here are about the two cases
where a naive implementation silently gives the wrong answer: a stage that raises, and a clock that
moves. Both produce numbers that look plausible and are not.
"""
from __future__ import annotations

import logging

import pytest

from libs.obs.timing import Timings, report, timed


def test_a_stage_records_how_long_it_took():
    timings = Timings()
    with timed("gather", timings):
        pass
    assert [stage for stage, _ms in timings.stages] == ["gather"]
    assert timings.stages[0][1] >= 0


def test_a_stage_that_raises_is_still_timed():
    """**The most useful timing there is.** A stage that runs for ninety seconds and then throws is
    exactly the one worth knowing about, and a context manager without a `finally` loses it."""
    timings = Timings()
    with pytest.raises(ValueError), timed("describe", timings):
        raise ValueError("upstream said no")

    assert [stage for stage, _ms in timings.stages] == ["describe"]


def test_a_failed_stage_is_logged_as_failed(caplog):
    """A slow stage and a slow *failing* stage are different problems. A duration alone says the
    first."""
    with caplog.at_level(logging.INFO), pytest.raises(ValueError), timed("embed", Timings()):
        raise ValueError("no")

    assert "outcome=failed" in caplog.text


def test_stages_keep_the_order_they_ran_in():
    """A stage that is fast but runs three times is a different problem from one that is slow once,
    and an unordered mapping hides the difference."""
    timings = Timings()
    for stage in ("gather", "ingest", "gather"):
        with timed(stage, timings):
            pass
    assert [stage for stage, _ms in timings.stages] == ["gather", "ingest", "gather"]


def test_the_slowest_stage_is_the_one_reported():
    timings = Timings()
    timings.record("gather", 10.0)
    timings.record("describe", 900.0)
    timings.record("embed", 40.0)

    assert timings.slowest() == ("describe", 900.0)
    assert timings.total_ms == 950.0


def test_an_empty_run_reports_no_slowest_stage_rather_than_raising():
    """A cycle that skipped every stage is a real outcome — the advisory lock was held — and the
    reporting path must survive it."""
    assert Timings().slowest() is None
    report(Timings(), event="harvest.timing")


def test_the_report_names_the_slowest_stage_explicitly(caplog):
    """The person reading this is reading it at the moment something is slow. Making them work it
    out from the other numbers is the wrong time to be terse."""
    timings = Timings()
    timings.record("gather", 10.0)
    timings.record("describe", 900.0)

    with caplog.at_level(logging.INFO):
        report(timings, event="harvest.timing", links=42)

    assert "slowest=describe" in caplog.text
    assert "slowest_ms=900" in caplog.text
    assert "total_ms=910" in caplog.text
    assert "links=42" in caplog.text


def test_durations_are_rounded_so_two_equal_runs_look_equal():
    """Sub-millisecond precision on a stage measured in seconds is noise that makes two identical
    runs look different."""
    timings = Timings()
    timings.record("gather", 10.4)
    timings.record("ingest", 10.6)
    assert timings.as_fields() == {"gather_ms": 10, "ingest_ms": 11, "total_ms": 21}
