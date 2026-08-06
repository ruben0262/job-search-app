"""Shadow scoring — having the judge grade the model, on a sample."""
from __future__ import annotations

import uuid as _uuid
from hashlib import sha256

# Percent of scored jobs also sent to the judge.
DEFAULT_SAMPLE_RATE = 10

# How far the two numbers may differ before the pair counts as a disagreement.
AGREEMENT_TOLERANCE = 15


def is_sampled(user_id: _uuid.UUID | str, job_id: int, *, rate: int = DEFAULT_SAMPLE_RATE) -> bool:
    """Whether this (user, job) pair is in the shadow sample."""
    if rate <= 0:
        return False
    if rate >= 100:
        return True
    digest = sha256(f"{user_id}:{job_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % 100 < rate


def agrees(model_score: float | None, judge_score: float | None, *,
           tolerance: int = AGREEMENT_TOLERANCE) -> bool | None:
    """Whether the two scores tell the same story."""
    if model_score is None or judge_score is None:
        return None
    return abs(float(model_score) - float(judge_score)) <= tolerance
