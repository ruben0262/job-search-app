"""The pre-score centroid filter: what it cuts, and what it must never cut."""
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
    """One row's score, expressed through the batch call the product actually uses."""
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
    """Cold start passes everything."""
    assert build(spread(MIN_POSITIVES - 1)) is None
    assert build([]) is None


def test_enough_positives_produces_a_centre_and_a_threshold():
    built = build(spread(MIN_POSITIVES + 20))
    assert built is not None
    centroid, threshold = built
    assert math.isclose(sum(c * c for c in centroid) ** 0.5, 1.0, rel_tol=1e-6), "unit vector"
    assert -1.0 <= threshold <= 1.0


def test_nothing_the_user_already_accepted_is_ever_cut():
    """The property the whole design rests on."""
    positives = spread(60)
    centroid, threshold = build(positives)

    assert all(keep([pack(v) for v in positives], centroid, threshold))


def test_the_margin_sits_below_the_least_similar_positive():
    """Stated as a relationship rather than a number."""
    positives = spread(60)
    centroid, threshold = build(positives)

    floor = min(similarity(vector, centroid) for vector in positives)
    assert math.isclose(threshold, floor - SAFETY_MARGIN, rel_tol=1e-6)


def test_something_pointing_the_other_way_is_cut():
    """The filter has to actually filter, or it is a dot product nobody needed."""
    centroid, threshold = build(spread(60))

    opposite = [-1.0] + [0.0] * 7
    assert keep([pack(opposite)], centroid, threshold) == [False]


def test_length_does_not_move_the_centre():
    """Vectors are normalised before averaging."""
    short = build([vec(1, 0, 0, 0, 0, 0, 0, 0)] * MIN_POSITIVES)
    long = build([vec(9, 0, 0, 0, 0, 0, 0, 0)] * MIN_POSITIVES)

    assert short is not None and long is not None
    for a, b in zip(short[0], long[0], strict=True):
        assert math.isclose(a, b, abs_tol=1e-6)


def test_a_re_embedded_corpus_refuses_rather_than_averaging_two_spaces():
    """Mixed widths mean the corpus was re-embedded underneath these labels."""
    mixed = [vec(1, 0, 0, 0, 0, 0, 0, 0)] * MIN_POSITIVES + [vec(1, 0, 0, 0)]
    assert build(mixed) is None


def test_a_vector_of_the_wrong_width_scores_zero_rather_than_raising():
    """A row embedded in another space must not take down a scoring run."""
    centroid, threshold = build(spread(60))

    # Wrong width and empty: dropped, not raised, and not mistaken for a match.
    assert keep([pack(vec(1, 0))], centroid, threshold) == [False]
    assert keep([b""], centroid, threshold) == [False]
    # And the rest of a batch is unaffected by one bad row.
    good = pack(spread(1)[0])
    assert keep([pack(vec(1, 0)), good], centroid, threshold) == [False, True]


def test_a_centroid_survives_the_round_trip_to_storage():
    """It is stored beside the model that produced it, in the same float32 format as an embedding."""
    centroid, _threshold = build(spread(40))

    restored = unpack(pack(centroid))
    assert len(restored) == len(centroid)
    for a, b in zip(centroid, restored, strict=True):
        assert math.isclose(a, b, rel_tol=1e-6)


def test_no_centroid_means_everything_is_scored():
    """The fail-open that matters most."""
    assert keep([pack(vec(1, 0, 0)), pack(vec(0, 1, 0))], [], 0.5) == [True, True]
    assert keep([], [0.1, 0.2], 0.5) == []


def test_the_batch_is_fast_enough_to_be_worth_running():
    """A filter that costs what it saves is not a filter."""
    import time

    centroid, threshold = build(spread(60, width=768))
    rows = [pack(spread(1, width=768)[0]) for _ in range(2_000)]

    started = time.perf_counter()
    keep(rows, centroid, threshold)
    per_row = (time.perf_counter() - started) / len(rows)

    # Measured at 4 microseconds on the development machine.
    assert per_row < 30e-6, f"{per_row * 1e6:.0f} microseconds per row is too slow to be worth it"
