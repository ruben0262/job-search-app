"""Accuracy gate (PLAN §4b/§14.7): a text embedded ALONE must equal the same text embedded
INSIDE a large mixed-length batch (within float tolerance). A failure means padding is leaking
into the pooled vector — a correctness bug in the batcher, not a tuning knob.

Integration test — needs a running stack (EMBEDDER_URL).
"""
from __future__ import annotations

import os

import httpx
import pytest

URL = os.environ.get("EMBEDDER_URL")
pytestmark = pytest.mark.skipif(not URL, reason="EMBEDDER_URL not set (no running stack)")

_TARGET = "senior backend engineer, Go, gRPC, Postgres, Kubernetes, remote within the EU"
_FILLERS = [
    "x",
    "junior python developer internship",
    "  ".join(["long padding text about unrelated responsibilities"] * 80),  # very long → forces padding
    "data scientist, pandas, scikit-learn",
]


def _embed(texts: list[str]) -> list[list[float]]:
    r = httpx.post(f"{URL}/embed", json={"texts": texts}, timeout=60)
    r.raise_for_status()
    return r.json()["vectors"]


def test_batch_invariance() -> None:
    alone = _embed([_TARGET])[0]
    in_batch = _embed([_FILLERS[0], _FILLERS[2], _TARGET, _FILLERS[1], _FILLERS[3]])[2]
    assert len(alone) == len(in_batch)
    max_delta = max(abs(a - b) for a, b in zip(alone, in_batch))
    assert max_delta < 1e-4, f"batch composition changed the vector (max |Δ|={max_delta:.2e})"
