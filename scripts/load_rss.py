#!/usr/bin/env python3
"""Load + memory gate (PLAN §14.5 / §12): drive realistic concurrent load at the shared embedder
while sampling the TEI container's RSS, and report peak.

The <2GB target is a claim about the box, not about the compose file — `mem_limit: 2g` is only a
ceiling that gets the container OOM-killed, which is exactly the incident this whole service exists
to prevent (PLAN §1). So measure it here BEFORE pointing production traffic at it.

Run ON THE VPS, where docker is:
    python3 scripts/load_rss.py --url http://localhost:8080 --callers 3 --corpus 2000

`--url` needs the gateway reachable; either publish 8080 temporarily or run this from a container
attached to embedder-net. RSS is read from `docker stats` (no extra deps).

Exit code 1 if peak RSS exceeds --budget-mb — usable as a CI/pre-deploy gate.
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import threading
import time
import urllib.error
import urllib.request

_JD = ("senior backend engineer, Go, gRPC, Postgres, Kubernetes; own service reliability, mentor "
       "engineers, remote within the EU. Responsibilities include design reviews and on-call. ")


def _post(url: str, path: str, texts: list[str], token: str | None, timeout: float):
    body = json.dumps({"texts": texts}).encode()
    req = urllib.request.Request(f"{url}{path}", data=body,
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _rss_mb(container: str) -> float | None:
    """Current RSS of a container, via `docker stats --no-stream` (e.g. '1.42GiB / 2GiB')."""
    try:
        out = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", container],
            capture_output=True, text=True, timeout=20, check=True).stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    used = out.split("/")[0].strip()
    for suffix, factor in (("GiB", 1024.0), ("MiB", 1.0), ("KiB", 1 / 1024.0), ("B", 1 / 1048576.0)):
        if used.endswith(suffix):
            try:
                return float(used[: -len(suffix)]) * factor
            except ValueError:
                return None
    return None


class _Sampler(threading.Thread):
    """Polls container RSS in the background; keeps the peak."""

    def __init__(self, container: str, interval: float = 1.0) -> None:
        super().__init__(daemon=True)
        self.container, self.interval = container, interval
        self.samples: list[float] = []
        # NOT `_stop`: threading.Thread already defines a private _stop() and join() calls it.
        self._stopped = threading.Event()

    def run(self) -> None:
        while not self._stopped.is_set():
            mb = _rss_mb(self.container)
            if mb is not None:
                self.samples.append(mb)
            self._stopped.wait(self.interval)

    def stop(self) -> None:
        self._stopped.set()
        self.join(timeout=30)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8080")
    p.add_argument("--token", default=None)
    p.add_argument("--container", default="tei", help="substring/name of the TEI container")
    p.add_argument("--callers", type=int, default=3, help="concurrent scoring callers (PLAN §12)")
    p.add_argument("--corpus", type=int, default=2000, help="texts in the cold-start bulk embed")
    p.add_argument("--budget-mb", type=float, default=2048.0)
    args = p.parse_args()

    sampler = _Sampler(args.container)
    sampler.start()

    score_latencies: list[float] = []
    errors: list[str] = []
    stop = threading.Event()

    def scorer() -> None:
        while not stop.is_set():
            t0 = time.perf_counter()
            try:
                _post(args.url, "/embed", [_JD + "one posting"], args.token, timeout=60)
                score_latencies.append(time.perf_counter() - t0)
            except (urllib.error.URLError, OSError, ValueError) as exc:
                errors.append(f"score: {exc}")
            time.sleep(0.2)

    threads = [threading.Thread(target=scorer, daemon=True) for _ in range(args.callers)]
    for t in threads:
        t.start()

    print(f"cold-start bulk embed: {args.corpus} texts (single request, silent for minutes on "
          f"CPU) — {args.callers} scoring callers running alongside ...", flush=True)
    t0 = time.perf_counter()
    try:
        _post(args.url, "/embed/bulk", [_JD * 3 + str(i) for i in range(args.corpus)],
              args.token, timeout=3600)
        bulk_s = time.perf_counter() - t0
    except (urllib.error.URLError, OSError, ValueError) as exc:
        errors.append(f"bulk: {exc}")
        bulk_s = float("nan")

    stop.set()
    for t in threads:
        t.join(timeout=60)
    sampler.stop()

    peak = max(sampler.samples) if sampler.samples else None
    print(f"\nbulk cold-start   : {bulk_s:.1f}s for {args.corpus} texts")
    if score_latencies:
        # quantiles() needs >= 2 points; a short run can produce fewer.
        p95 = (statistics.quantiles(score_latencies, n=20)[-1] if len(score_latencies) > 1
               else score_latencies[0])
        print(f"score latency     : median {statistics.median(score_latencies):.3f}s  "
              f"p95 {p95:.3f}s  max {max(score_latencies):.3f}s  (n={len(score_latencies)})")
    print(f"errors            : {len(errors)}" + (f"  first={errors[0]}" if errors else ""))
    if peak is None:
        print(f"peak RSS          : UNKNOWN — no docker stats for container {args.container!r}")
        return 1
    print(f"peak RSS ({args.container:<8}): {peak:.0f} MB   budget {args.budget_mb:.0f} MB")
    if peak > args.budget_mb:
        print("FAIL — over budget. Lower TEI_MAX_BATCH_TOKENS / EMBEDDER_BULK_CHUNK (PLAN §12).")
        return 1
    print("OK")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
