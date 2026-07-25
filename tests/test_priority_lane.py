"""Priority-lane gate (PLAN §14.8 / §4c): under a training flood on the BULK lane, a single
on-arrival score request on the FAST lane must still come back promptly.

This is the whole justification for running one TEI instance with a gateway in front of it instead
of two containers (which would double RAM against the <2GB budget, PLAN §12). If the fast lane
queues behind the bulk lane, that design choice is not paying for itself.

Integration test — needs a running stack (EMBEDDER_URL). Tune the budget on the real box:
    SCORE_LATENCY_BUDGET_S=2.0 pytest tests/test_priority_lane.py -v
"""
from __future__ import annotations

import os
import statistics
import threading
import time

import httpx
import pytest

URL = os.environ.get("EMBEDDER_URL")
TOKEN = os.environ.get("EMBEDDER_TOKEN") or None
BUDGET_S = float(os.environ.get("SCORE_LATENCY_BUDGET_S", "5.0"))
FLOOD_TEXTS = int(os.environ.get("FLOOD_TEXTS", "512"))
FLOOD_WORKERS = int(os.environ.get("FLOOD_WORKERS", "2"))

pytestmark = pytest.mark.skipif(not URL, reason="EMBEDDER_URL not set (no running stack)")

_HEADERS = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
_JD = ("senior backend engineer, Go, gRPC, Postgres, Kubernetes; you will own service reliability "
       "and mentor engineers. Remote within the EU. ")


def _post(path: str, texts: list[str], timeout: float) -> httpx.Response:
    return httpx.post(f"{URL}{path}", json={"texts": texts}, headers=_HEADERS, timeout=timeout)


def _score_latency() -> float:
    t0 = time.perf_counter()
    r = _post("/embed", ["backend engineer, Go and Postgres, fully remote EU"], timeout=BUDGET_S * 4)
    r.raise_for_status()
    return time.perf_counter() - t0


def test_score_lane_bypasses_training_flood() -> None:
    baseline = statistics.median([_score_latency() for _ in range(3)])

    stop = threading.Event()
    errors: list[Exception] = []
    corpus = [_JD * 4 + str(i) for i in range(FLOOD_TEXTS)]

    def flood() -> None:
        while not stop.is_set():
            try:
                # 503 = backpressure (queue bound, PLAN §4c) — a valid answer, not a failure.
                r = _post("/embed/bulk", corpus, timeout=300)
                if r.status_code not in (200, 503):
                    r.raise_for_status()
            except Exception as exc:                       # noqa: BLE001 — reported after join
                errors.append(exc)
                return

    threads = [threading.Thread(target=flood, daemon=True) for _ in range(FLOOD_WORKERS)]
    for t in threads:
        t.start()
    try:
        time.sleep(1.0)                                    # let the bulk lane fill the queue
        under_load = [_score_latency() for _ in range(5)]
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=300)

    assert not errors, f"bulk flood errored: {errors[0]!r}"
    worst = max(under_load)
    assert worst < BUDGET_S, (
        f"score lane starved by the bulk lane: worst {worst:.2f}s > budget {BUDGET_S}s "
        f"(idle baseline {baseline:.2f}s, latencies {[round(x, 2) for x in under_load]}). "
        "Lower EMBEDDER_BULK_CHUNK so bulk work is split finer, or raise the budget if this is "
        "acceptable on this box (PLAN §14.8).")
