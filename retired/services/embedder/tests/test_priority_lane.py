"""Priority-lane gate (PLAN §14.8): a fast-lane request stays prompt under a bulk training flood."""
from __future__ import annotations

import os
import statistics
import threading
import time

import httpx
import pytest

URL = os.environ.get("EMBEDDER_URL")
TOKEN = os.environ.get("EMBEDDER_TOKEN") or None
BUDGET_S = float(os.environ.get("SCORE_LATENCY_BUDGET_S", "10.0"))
# Enough sustained bulk work to cover the measurement window, not a whole training corpus.
FLOOD_TEXTS = int(os.environ.get("FLOOD_TEXTS", "128"))
FLOOD_WORKERS = int(os.environ.get("FLOOD_WORKERS", "2"))
FLOOD_TIMEOUT_S = float(os.environ.get("FLOOD_TIMEOUT_S", "600"))

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
    timeouts: list[str] = []
    corpus = [_JD * 4 + str(i) for i in range(FLOOD_TEXTS)]

    def flood() -> None:
        while not stop.is_set():
            try:
                # 503 = backpressure (queue bound, PLAN §4c) — a valid answer, not a failure.
                r = _post("/embed/bulk", corpus, timeout=FLOOD_TIMEOUT_S)
                if r.status_code not in (200, 503):
                    r.raise_for_status()
            except httpx.ReadTimeout:
                # The flood is background LOAD, not the thing under test.
                timeouts.append("bulk request outlived the measurement window")
                return
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
            # Don't block on an in-flight bulk request finishing; threads are daemons.
            t.join(timeout=5)

    assert not errors, f"bulk flood errored: {errors[0]!r}"
    worst = max(under_load)
    print(f"\nscore latency — idle baseline {baseline:.2f}s | under flood: "
          f"median {statistics.median(under_load):.2f}s, worst {worst:.2f}s "
          f"(budget {BUDGET_S}s, {len(timeouts)} bulk request(s) still running)")
    assert worst < BUDGET_S, (
        f"score lane starved by the bulk lane: worst {worst:.2f}s > budget {BUDGET_S}s "
        f"(idle baseline {baseline:.2f}s, latencies {[round(x, 2) for x in under_load]}). "
        "Lower EMBEDDER_BULK_CHUNK so bulk work is split finer, or raise the budget if this is "
        "acceptable on this box (PLAN §14.8).")
