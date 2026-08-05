"""The pre-score centroid filter: what it cuts, and what it must never cut.

**The dangerous failure here is silent and expensive**: a filter that drops jobs a user wanted looks
exactly like a quiet week. So the tests that matter are the ones asserting nothing is lost — a
posting the user has already accepted, an account too new to have a centre, a corpus re-embedded
underneath the labels.

No database and no numpy: these are decisions the library makes from vectors, and a test that skips
itself without Postgres is a test CI never runs (CLAUDE.md §6).
"""
from __future__ import annotations

import math

from libs.scoring.centroid import (
    MIN_POSITIVES,
    SAFETY_MARGIN,
    build,
    keep,
    pack,
    unpack,
)


def similarity(vector, centroid) -> float:
    """One row's score, expressed through the batch call the product actually uses.

    Deliberately not a second implementation: `keep` is the only arithmetic, and a test helper that
    re-did the dot product would be free to agree with itself while both were wrong.
    """
    if not centroid or not vector or len(vector) != len(centroid):
        return 0.0
    import numpy

    unit = numpy.asarray(vector, dtype=numpy.float32)
    norm = float(numpy.linalg.norm(unit)) or 1.0
    return float((unit / norm) @ numpy.asarray(centroid, dtype=numpy.float32))


def vec(*components: float) -> list[float]:
    return list(components)


def spread(count: int, *, width: int = 8, jitter: float = 0.05) -> list[list[float]]:
    """`count` vectors clustered around one direction, so they have a real centre."""
    out = []
    for index in range(count):
        vector = [1.0] + [0.0] * (width - 1)
        vector[1 + index % (width - 1)] += jitter * (1 + index % 3)
        out.append(vector)
    return out


def test_a_new_account_is_not_filtered_at_all():
    """**Cold start passes everything.** A centroid of a handful of vectors points at wherever those
    few happened to be, and filtering on it would hide the corpus from the accounts least able to
    tell it was happening."""
    assert build(spread(MIN_POSITIVES - 1)) is None
    assert build([]) is None


def test_enough_positives_produces_a_centre_and_a_threshold():
    built = build(spread(MIN_POSITIVES + 20))
    assert built is not None
    centroid, threshold = built
    assert math.isclose(sum(c * c for c in centroid) ** 0.5, 1.0, rel_tol=1e-6), "unit vector"
    assert -1.0 <= threshold <= 1.0


def test_nothing_the_user_already_accepted_is_ever_cut():
    """**The property the whole design rests on.** The threshold is the least similar job they have
    said yes to, less a margin — so every one of their own positives clears it, by construction.
    Measured on live data at the floor with no margin, one account lost 16 of 280 matches; the
    margin is what took that to zero.
    """
    positives = spread(60)
    centroid, threshold = build(positives)

    assert all(keep([pack(v) for v in positives], centroid, threshold))


def test_the_margin_sits_below_the_least_similar_positive():
    """Stated as a relationship rather than a number, so the constant can be retuned from new
    measurements without rewriting the test that guards it."""
    positives = spread(60)
    centroid, threshold = build(positives)

    floor = min(similarity(vector, centroid) for vector in positives)
    assert math.isclose(threshold, floor - SAFETY_MARGIN, rel_tol=1e-6)


def test_something_pointing_the_other_way_is_cut():
    """The filter has to actually filter, or it is a dot product nobody needed. Asserting the
    presence of an effect, not just the absence of an error (CLAUDE.md §2)."""
    centroid, threshold = build(spread(60))

    opposite = [-1.0] + [0.0] * 7
    assert keep([pack(opposite)], centroid, threshold) == [False]


def test_length_does_not_move_the_centre():
    """Vectors are normalised before averaging: a posting with a longer description must not pull
    the centre towards itself. The question is direction, not magnitude."""
    short = build([vec(1, 0, 0, 0, 0, 0, 0, 0)] * MIN_POSITIVES)
    long = build([vec(9, 0, 0, 0, 0, 0, 0, 0)] * MIN_POSITIVES)

    assert short is not None and long is not None
    for a, b in zip(short[0], long[0], strict=True):
        assert math.isclose(a, b, abs_tol=1e-6)


def test_a_re_embedded_corpus_refuses_rather_than_averaging_two_spaces():
    """Mixed widths mean the corpus was re-embedded underneath these labels. Averaging across two
    vector spaces produces a confident direction in neither, and the scorer already fails closed on
    a version mismatch for the same reason."""
    mixed = [vec(1, 0, 0, 0, 0, 0, 0, 0)] * MIN_POSITIVES + [vec(1, 0, 0, 0)]
    assert build(mixed) is None


def test_a_vector_of_the_wrong_width_scores_zero_rather_than_raising():
    """A row embedded in another space must not take down a scoring run. Zero is below every real
    threshold, so it is skipped rather than crashing the lane."""
    centroid, threshold = build(spread(60))

    # Wrong width and empty: dropped, not raised, and not mistaken for a match.
    assert keep([pack(vec(1, 0))], centroid, threshold) == [False]
    assert keep([b""], centroid, threshold) == [False]
    # And the rest of a batch is unaffected by one bad row.
    good = pack(spread(1)[0])
    assert keep([pack(vec(1, 0)), good], centroid, threshold) == [False, True]


def test_a_centroid_survives_the_round_trip_to_storage():
    """It is stored beside the model that produced it, in the same float32 format as an embedding —
    one format, one reader, no second definition."""
    centroid, _threshold = build(spread(40))

    restored = unpack(pack(centroid))
    assert len(restored) == len(centroid)
    for a, b in zip(centroid, restored, strict=True):
        assert math.isclose(a, b, rel_tol=1e-6)


def test_no_centroid_means_everything_is_scored():
    """**The fail-open that matters most.** A caller holding no centroid — an untrained account, a
    model from before this existed — must score every row rather than none. Getting this backwards
    would silently empty a feed, which is the failure mode this project keeps hitting."""
    assert keep([pack(vec(1, 0, 0)), pack(vec(0, 1, 0))], [], 0.5) == [True, True]
    assert keep([], [0.1, 0.2], 0.5) == []


def test_the_batch_is_fast_enough_to_be_worth_running():
    """**A filter that costs what it saves is not a filter.** The plain-Python dot product was 235
    microseconds a row — 5.5 seconds for one account's corpus, per user. This must stay far under
    the model call it protects."""
    import time

    centroid, threshold = build(spread(60, width=768))
    rows = [pack(spread(1, width=768)[0]) for _ in range(2_000)]

    started = time.perf_counter()
    keep(rows, centroid, threshold)
    per_row = (time.perf_counter() - started) / len(rows)

    # Measured at 4 microseconds on the development machine; the bound is loose enough that a
    # slower CI runner does not fail the build, and tight enough to catch a return to per-row
    # Python arithmetic, which was 235.
    assert per_row < 30e-6, f"{per_row * 1e6:.0f} microseconds per row is too slow to be worth it"
