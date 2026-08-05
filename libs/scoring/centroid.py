"""The cheap pre-score filter: how far a posting sits from what a user has already said yes to.

**The question this answers is "is it worth running the model on this row at all".** Scoring is the
cost that scales worst on this platform — one model call per user per posting — and most of what it
runs on was never going to be a match. A dot product against a single vector answers that for a
thousandth of the price.

## Why a centroid rather than the field taxonomy 6z designed

Both were on the table and the taxonomy lost on the platform's current size (ROADMAP 6z.1). It
derives job fields from which keyword found which posting, and measurement killed it for now:
98.5% of every harvest is a duplicate whose keyword is discarded, 39% of rows come from sources that
take no keyword at all, and the whole label space is 28 terms across two accounts. A centroid needs
none of that — every row already has a vector and every trained user already has labels.

**The taxonomy is still the better answer at scale**, because it is computed once and shared while
this is per user. That trade is written down in 6z.1 with the trigger for revisiting it.

## The two rules, and both are measured rather than chosen

1. **The threshold is the user's own.** A platform constant cannot work: at 0.66 the same number cut
   51% of one live account's pool and 20% of the other's, because a user with broad interests has a
   broader spread. So it is derived from the similarity of that user's *own* positives — "no job is
   cut that sits closer to my centre than a job I have already accepted".
2. **Unknown passes, and so does everything when we cannot tell.** Under `MIN_POSITIVES` the filter
   does not engage at all. A thin centroid is a confidently wrong one, and this project's rule
   throughout is that a filter which cannot be sure keeps the row.

Measured on the live corpus, 23,621 scored rows per account, at `SAFETY_MARGIN`:

| account | positives | pool cut | matches lost |
|---|---|---|---|
| narrow interests | 67 | **72%** | **0** |
| broad interests | 878 | 4% | **0** |

The asymmetry is the design working, not a defect: an account whose accepted jobs span a wide space
legitimately cannot be filtered much, and it is not filtered much.
"""
from __future__ import annotations

import struct

# A label is 0-100 and this is the score at which one counts as "the kind of job I want". The
# trainer's own fit threshold, imported rather than restated — a second definition of "positive" is
# the defect this project ships most often (ROADMAP 6ah).
from libs.scoring.thresholds import DEFAULT_CUTOFF

POSITIVE_AT = DEFAULT_CUTOFF

# Below this many positive labels the filter does not run. A centroid of a handful of vectors points
# at wherever those few happened to be, and 25 is the same floor the trainer requires before it will
# fit a model at all — an account with fewer is in cold start, where nothing should be filtered.
MIN_POSITIVES = 25

# How far below the least-similar positive to put the threshold.
#
# **Measured, not chosen.** At the floor exactly, the narrow account lost 16 of 280 matches — the
# floor is the minimum of a sample, so it sits right on the edge of the distribution and any new job
# slightly beyond it is cut. Stepping back 0.05 lost **zero** on both live accounts while still
# cutting 72% of the narrow one's pool. The cost of the margin is a few percent of pool; the cost of
# not having it is a job somebody wanted.
SAFETY_MARGIN = 0.05


def pack(vector: list[float]) -> bytes:
    """Store a centroid the same way a posting's embedding is stored. One format, one reader."""
    return struct.pack(f"<{len(vector)}f", *vector)


def unpack(blob: bytes) -> list[float]:
    """Read one back."""
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


def _numpy():
    """**Imported lazily, because `libs.scoring` is imported by services without numpy.**

    `capture`, `docgen` and `labeller` all import this package for thresholds and rules and ship no
    numpy at all; only the trainer and the scorer — the two that touch vectors — do. A module-level
    import here would break three images to speed up two.
    """
    import numpy  # noqa: PLC0415 - see above

    return numpy


def _units(vectors, numpy):
    """Rows as unit vectors. Direction is the question; a longer description is not a stronger one."""
    matrix = numpy.asarray(vectors, dtype=numpy.float32)
    if matrix.ndim == 1:
        matrix = matrix.reshape(1, -1)
    norms = numpy.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def build(positives: list[list[float]]) -> tuple[list[float], float] | None:
    """The centroid of a user's accepted jobs, and the similarity threshold below which to skip.

    Returns `None` when there are too few positives to be sure — which the caller must treat as
    "do not filter", never as "filter everything".

    Each vector is normalised **before** averaging, so a posting with a longer description does not
    pull the centre towards itself: the question is direction, not magnitude.
    """
    usable = [vector for vector in positives if vector]
    if len(usable) < MIN_POSITIVES:
        return None
    width = len(usable[0])
    if any(len(vector) != width for vector in usable):
        # Mixed widths mean the corpus was re-embedded underneath these labels. Refusing is right:
        # averaging across two vector spaces produces a confident direction in neither, and the
        # scorer already fails closed on a version mismatch for the same reason.
        return None

    numpy = _numpy()
    units = _units(usable, numpy)
    centroid = units.mean(axis=0)
    length = float(numpy.linalg.norm(centroid))
    if length == 0:
        return None
    centroid = centroid / length

    # The least similar job this user has accepted, minus the margin. Computed against the finished
    # centroid rather than incrementally, because that is what the filter compares to.
    floor = float((units @ centroid).min())
    return [float(component) for component in centroid], floor - SAFETY_MARGIN


def keep(blobs: list[bytes], centroid: list[float], threshold: float) -> list[bool]:
    """Which of these postings are worth scoring. One matrix-vector product for the whole batch.

    **Takes the stored format, because converting is the whole cost.** A plain-Python dot product
    over 768 floats is 235 microseconds a row — 5.5 seconds for one account's corpus, per user. Going
    to numpy but passing lists of Python floats is still 25, because building the array dominates.
    Reading the packed bytes straight into one array with `frombuffer` is **4 microseconds a row**
    measured over 20,000 — 57 times the plain-Python version — and `corpus.embedding` is already
    those bytes, so nothing is unpacked in order to be re-packed (ROADMAP 6an).

    A blob that is not the centroid's width is dropped rather than raising — a row embedded in
    another vector space must not take down a scoring run — and an empty centroid keeps everything,
    because no opinion must never mean "filter all".
    """
    if not blobs:
        return []
    if not centroid:
        return [True] * len(blobs)

    numpy = _numpy()
    width = len(centroid)
    stride = width * 4              # float32
    usable = [index for index, blob in enumerate(blobs) if blob and len(blob) == stride]
    out = [False] * len(blobs)
    if not usable:
        return out

    matrix = numpy.frombuffer(b"".join(blobs[index] for index in usable),
                              dtype=numpy.float32).reshape(len(usable), width)
    norms = numpy.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    scores = (matrix / norms) @ numpy.asarray(centroid, dtype=numpy.float32)

    for position, index in enumerate(usable):
        out[index] = bool(scores[position] >= threshold)
    return out
