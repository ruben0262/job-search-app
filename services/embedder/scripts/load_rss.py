#!/usr/bin/env python3
"""Load + memory gate (PLAN §14.5): drive concurrent load and report the backend's peak RSS."""
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
    bulk_desc = f"{bulk_s:.1f}s" if bulk_s == bulk_s else "FAILED"   # NaN != NaN
    print(f"\nbulk cold-start   : {bulk_desc} for {args.corpus} texts")
    if score_latencies:
        # quantiles() needs >= 2 points; a short run can produce fewer.
        p95 = (statistics.quantiles(score_latencies, n=20)[-1] if len(score_latencies) > 1
               else score_latencies[0])
        print(f"score latency     : median {statistics.median(score_latencies):.3f}s  "
              f"p95 {p95:.3f}s  max {max(score_latencies):.3f}s  (n={len(score_latencies)})")
    print(f"errors            : {len(errors)}" + (f"  first={errors[0]}" if errors else ""))
    if peak is None:
        print(f"peak RSS          : UNKNOWN — no docker stats for container {args.container!r}")
    else:
        print(f"peak RSS ({args.container:<8}): {peak:.0f} MB   budget {args.budget_mb:.0f} MB")

    # Report the verdict LAST and only once, so a run with failed requests can never print OK.
    if errors:
        print(f"\nFAIL — {len(errors)} request(s) failed; the RSS figure above is meaningless "
              f"because the load never ran.\n  Is the gateway up and published?  "
              f"docker compose ps  /  docker compose logs embedder-gateway")
        return 1
    if peak is None:
        print(f"\nFAIL — could not read RSS. Is the container named {args.container!r}? "
              "Check `docker compose ps`, and that your user is in the `docker` group.")
        return 1
    if peak > args.budget_mb:
        print("\nFAIL — over budget. Lower TEI_MAX_BATCH_TOKENS / EMBEDDER_BULK_CHUNK (PLAN §12).")
        return 1
    print("\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
