"""Integration test — needs a running stack. Set EMBEDDER_URL (e.g. http://localhost:8080)
after publishing the gateway port locally. Skips when unset so CI without a stack stays green.
"""
from __future__ import annotations

import os

import httpx
import pytest

URL = os.environ.get("EMBEDDER_URL")
pytestmark = pytest.mark.skipif(not URL, reason="EMBEDDER_URL not set (no running stack)")


def test_health_contract() -> None:
    r = httpx.get(f"{URL}/health", timeout=30)
    r.raise_for_status()
    h = r.json()
    for key in ("ok", "model", "dim", "normalize", "version"):
        assert key in h, f"/health missing '{key}'"
    assert isinstance(h["dim"], int) and h["dim"] > 0
