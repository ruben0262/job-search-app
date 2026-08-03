

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
