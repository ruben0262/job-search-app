"""Parity gate (PLAN §14.1): does the shared embedder produce the SAME vector as the old
in-process fastembed path? This decides whether existing classifiers keep working as-is.

Two ways forward if parity FAILS (TEI's pooling/normalisation differs from fastembed's):
  1. Configure TEI to match fastembed, OR
  2. Adopt TEI as the single source of truth and RETRAIN every classifier on TEI vectors
     (the clean cut — training and serving are then self-consistent, parity-with-fastembed
     becomes irrelevant). Since standardising the shared model already forces a retrain for the
     off-model tenant (PLAN §10), option 2 is usually the pragmatic path.

Needs a running stack (EMBEDDER_URL) AND fastembed installed with the SAME model. Skips otherwise.
"""
from __future__ import annotations

import os

import httpx
import pytest

URL = os.environ.get("EMBEDDER_URL")
MODEL = os.environ.get("EMBEDDER_MODEL", "BAAI/bge-small-en-v1.5")

pytestmark = pytest.mark.skipif(not URL, reason="EMBEDDER_URL not set (no running stack)")

_SAMPLES = [
    "backend engineer, Go and Postgres, fully remote EU",
    "machine learning engineer, PyTorch, LLMs, hybrid Berlin",
    "junior software developer, graduate scheme, London",
]


def _remote(texts: list[str]) -> list[list[float]]:
    r = httpx.post(f"{URL}/embed", json={"texts": texts}, timeout=60)
    r.raise_for_status()
    return r.json()["vectors"]


def test_parity_with_fastembed() -> None:
    fastembed = pytest.importorskip("fastembed", reason="fastembed not installed")
    import numpy as np

    local = list(fastembed.TextEmbedding(model_name=MODEL).embed(_SAMPLES))
    remote = _remote(_SAMPLES)
    for i, (a, b) in enumerate(zip(local, remote)):
        a = np.asarray(a, dtype=np.float32)
        b = np.asarray(b, dtype=np.float32)
        cos = float(a @ b / ((np.linalg.norm(a) * np.linalg.norm(b)) or 1.0))
        # Loud, informative failure — the team decides match-or-retrain from the number.
        assert cos > 0.9999, (
            f"sample {i}: cosine {cos:.6f} — TEI vector != fastembed vector. "
            "Either match TEI's pooling/normalisation or retrain classifiers on TEI (see docstring).")
