"""The cheap pre-score filter: how far a posting sits from what a user has already said yes to."""
from __future__ import annotations

import struct

# A label is 0-100 and this is the score at which one counts as "the kind of job I want".
from libs.scoring.thresholds import DEFAULT_CUTOFF

POSITIVE_AT = DEFAULT_CUTOFF

# Below this many positive labels the filter does not run.
MIN_POSITIVES = 25

# How far below the least-similar positive to put the threshold.
SAFETY_MARGIN = 0.05


def pack(vector: list[float]) -> bytes:
    """Store a centroid the same way a posting's embedding is stored. One format, one reader."""
    return struct.pack(f"<{len(vector)}f", *vector)


def unpack(blob: bytes) -> list[float]:
    """Read one back."""
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


def _numpy():
    """Imported lazily, because `libs.scoring` is imported by services without numpy."""
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
    """The centroid of a user's accepted jobs, and the similarity threshold below which to skip."""
    usable = [vector for vector in positives if vector]
    if len(usable) < MIN_POSITIVES:
        return None
    width = len(usable[0])
    if any(len(vector) != width for vector in usable):
        # Mixed widths mean the corpus was re-embedded underneath these labels.
        return None

    numpy = _numpy()
    units = _units(usable, numpy)
    centroid = units.mean(axis=0)
    length = float(numpy.linalg.norm(centroid))
    if length == 0:
        return None
    centroid = centroid / length

    # The least similar job this user has accepted, minus the margin.
    floor = float((units @ centroid).min())
    return [float(component) for component in centroid], floor - SAFETY_MARGIN


def keep(blobs: list[bytes], centroid: list[float], threshold: float) -> list[bool]:
    """Which of these postings are worth scoring."""
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
