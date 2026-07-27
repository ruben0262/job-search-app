#!/usr/bin/env python3
"""End-to-end check that the shared embedder and every tenant on it actually work.

Answers the question "is this really working?" with evidence rather than a green container:

  1. The embedder confirms its vector space against the live backend (not just its .env).
  2. Each tenant's scorer-service reports ready — which now includes reaching the embedder.
  3. Each tenant is on the SAME model the embedder serves (a mismatch here is the silent-wrong-
     scores failure the whole version contract exists to prevent).
  4. Each tenant returns real scores for a real POST /score — the only proof the full path works.
  5. No tenant has an embedding model resident (that is the entire point of sharing one).

Run on the VPS, stdlib only, no venv needed:

    python3 services/embedder/scripts/verify_stack.py

Add --failure-drill to also prove fail-closed + self-recovery. That one STOPS the embedder
briefly, so every tenant loses scoring for ~30s; it is opt-in for that reason.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

DEFAULT_TENANTS = [
    "job-search=job-search-scorer-service-1",
    "andras=andras-job-search-scorer-service-1",
]
SAMPLE_JOBS = [
    {"url": "https://example.test/a", "title": "Senior Backend Engineer",
     "description": "Go, gRPC, Postgres and Kubernetes. You will own service reliability, run "
                    "design reviews and mentor engineers. Fully remote within the EU. " * 3},
    {"url": "https://example.test/b", "title": "Warehouse Operative",
     "description": "Night shift picking and packing in a distribution centre. No experience "
                    "needed, on-site only, forklift licence a plus. " * 3},
]

PASS, FAIL = "PASS", "FAIL"
_results: list[tuple[str, str, str]] = []


def record(status: str, name: str, detail: str = "") -> bool:
    _results.append((status, name, detail))
    mark = "  ok " if status == PASS else "  XX "
    print(f"{mark}{name}" + (f"\n       {detail}" if detail else ""), flush=True)
    return status == PASS


def get_json(url: str, timeout: float = 15.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def gateway_health(url: str, container: str) -> tuple[dict, str]:
    """Read the gateway's /health, via the host port if published, else from inside the network.

    The host port is a convenience for tooling, not part of how the service is consumed — tenants
    reach the gateway by service name over embedder-net. So this falls back to exec'ing into the
    gateway container, which means the port can stay closed without blinding the checks. Returns
    (health, how) so the output says which path answered.
    """
    try:
        return get_json(f"{url}/health"), f"host {url}"
    except (urllib.error.URLError, OSError, ValueError):
        pass
    rc, out = docker("exec", container, "python", "-c",
                     "import httpx;print(httpx.get('http://localhost:8080/health',"
                     "timeout=15).text)")
    if rc != 0:
        raise RuntimeError(f"host port unreachable and `docker exec {container}` failed: {out}")
    return json.loads(out.strip().splitlines()[-1]), f"container {container}"


def docker(*args: str, timeout: float = 60.0) -> tuple[int, str]:
    p = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout or p.stderr).strip()


def in_container(container: str, code: str, timeout: float = 120.0) -> tuple[int, str]:
    """Run python inside a tenant container — tenants publish no host port, so this is the only
    way to reach their scorer-service from outside the docker network."""
    return docker("exec", container, "python", "-c", code, timeout=timeout)


def tenant_health(container: str) -> dict:
    rc, out = in_container(container, (
        "import httpx,json;"
        "print(json.dumps(httpx.get('http://localhost:8000/health',timeout=30).json()))"))
    if rc != 0:
        raise RuntimeError(out)
    return json.loads(out.strip().splitlines()[-1])


def tenant_score(container: str) -> list[dict]:
    # base64 the payload rather than interpolating JSON into a shell-bound -c string: job text is
    # arbitrary and quoting it by hand is how this kind of helper silently breaks later.
    import base64
    payload = base64.b64encode(json.dumps({"jobs": SAMPLE_JOBS}).encode()).decode()
    rc, out = in_container(container, (
        "import httpx,json,base64;"
        f"body=json.loads(base64.b64decode('{payload}'));"
        "r=httpx.post('http://localhost:8000/score',json=body,timeout=300);"
        "r.raise_for_status();print(json.dumps(r.json()))"), timeout=360)
    if rc != 0:
        raise RuntimeError(out)
    return json.loads(out.strip().splitlines()[-1])["results"]


def rss_mb(container: str) -> float | None:
    rc, out = docker("stats", "--no-stream", "--format", "{{.MemUsage}}", container)
    if rc != 0:
        return None
    used = out.split("/")[0].strip()
    for suffix, factor in (("GiB", 1024.0), ("MiB", 1.0), ("KiB", 1 / 1024.0)):
        if used.endswith(suffix):
            try:
                return float(used[: -len(suffix)]) * factor
            except ValueError:
                return None
    return None


def check_embedder(url: str, container: str) -> dict | None:
    print("\n-- embedder ---------------------------------------------")
    try:
        h, how = gateway_health(url, container)
    except Exception as exc:                                    # noqa: BLE001
        record(FAIL, "embedder reachable", str(exc)[:300])
        return None
    record(PASS, "embedder reachable", f"via {how}")
    if not h.get("verified"):
        record(FAIL, "vector space confirmed against live backend",
               f"state={h.get('verify_state')} {h.get('verify_detail')}")
        return None
    record(PASS, "vector space confirmed against live backend", h.get("verify_detail", ""))
    record(PASS if h.get("ok") else FAIL, "embedder /health ok",
           f"model={h.get('model')} dim={h.get('dim')} queues={h.get('queue_depths')}")
    return h


def check_tenant(name: str, container: str, embedder: dict, max_rss_mb: float) -> None:
    print(f"\n-- tenant: {name} ({container}) -------------------------")
    rc, _ = docker("inspect", "-f", "{{.State.Running}}", container)
    if rc != 0:
        record(FAIL, f"{name}: container exists", f"no container named {container}")
        return

    try:
        h = tenant_health(container)
    except Exception as exc:                                    # noqa: BLE001
        record(FAIL, f"{name}: scorer-service /health", str(exc)[:300])
        return

    if not h.get("ok"):
        record(FAIL, f"{name}: scorer-service ready", h.get("error", str(h))[:300])
        return
    record(PASS, f"{name}: scorer-service ready",
           f"model_type={h.get('model_type')} n_train={h.get('n_train')} cutoff={h.get('cutoff')}")

    # The contract: this tenant's classifier and the shared embedder must be the same vector space.
    if h.get("embed_model") != embedder.get("model"):
        record(FAIL, f"{name}: on the shared model",
               f"artifact={h.get('embed_model')} embedder={embedder.get('model')} — RETRAIN NEEDED")
    else:
        record(PASS, f"{name}: on the shared model", str(h.get("embed_model")))

    # The only real proof: a genuine score round-trip through the embedder and back.
    try:
        results = tenant_score(container)
    except Exception as exc:                                    # noqa: BLE001
        record(FAIL, f"{name}: POST /score round-trip", str(exc)[:300])
        return
    scores = [r.get("score") for r in results]
    if len(results) != len(SAMPLE_JOBS) or any(not isinstance(s, (int, float)) for s in scores):
        record(FAIL, f"{name}: POST /score round-trip", f"unexpected results: {results}")
    else:
        record(PASS, f"{name}: POST /score round-trip",
               f"scores={scores} decisions={[r.get('decision') for r in results]}")

    # No model resident is the entire point. A tenant that quietly reloaded fastembed would still
    # score correctly — and still cost the ~1.5GB this project exists to remove.
    mb = rss_mb(container)
    if mb is None:
        record(FAIL, f"{name}: memory readable", "docker stats gave nothing")
    elif mb > max_rss_mb:
        record(FAIL, f"{name}: no model resident",
               f"{mb:.0f} MB > {max_rss_mb:.0f} MB — is fastembed back in this image?")
    else:
        record(PASS, f"{name}: no model resident", f"{mb:.0f} MB")


def failure_drill(url: str, gateway: str, tenants: list[tuple[str, str]],
                  compose_dir: str) -> None:
    print("\n-- failure drill (embedder stopped) ---------------------")
    stopped = subprocess.run(["docker", "compose", "stop"], cwd=compose_dir,
                             capture_output=True, text=True)
    if stopped.returncode != 0:
        record(FAIL, "stop embedder", (stopped.stderr or "could not stop the stack")[:200])
        return
    try:
        time.sleep(3)
        for name, container in tenants:
            try:
                h = tenant_health(container)
            except Exception as exc:                            # noqa: BLE001
                record(FAIL, f"{name}: reports unhealthy while embedder is down", str(exc)[:200])
                continue
            # Reporting ok here would be the dangerous outcome: the pipeline would proceed and
            # then fail every job, or worse, a fallback would score against the wrong vectors.
            if h.get("ok"):
                record(FAIL, f"{name}: reports unhealthy while embedder is down",
                       "still says ok:true — health is not checking the embedder")
            else:
                record(PASS, f"{name}: reports unhealthy while embedder is down",
                       str(h.get("error"))[:160])
    finally:
        subprocess.run(["docker", "compose", "up", "-d"], cwd=compose_dir,
                       capture_output=True, text=True)

    print("\n-- recovery (embedder back, NO restarts) ----------------")
    deadline = time.time() + 120
    while time.time() < deadline:
        try:
            if gateway_health(url, gateway)[0].get("ok"):
                break
        except Exception:                                       # noqa: BLE001
            pass
        time.sleep(3)
    for name, container in tenants:
        ok = False
        deadline = time.time() + 60
        while time.time() < deadline and not ok:
            try:
                ok = bool(tenant_health(container).get("ok"))
            except Exception:                                   # noqa: BLE001
                ok = False
            if not ok:
                time.sleep(5)
        record(PASS if ok else FAIL, f"{name}: recovered without a restart",
               "" if ok else "still not ready after 60s")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8080",
                   help="host port if published; falls back to --gateway-container when closed")
    p.add_argument("--gateway-container", default="job-search-app-embedder-gateway-1")
    p.add_argument("--tenant", action="append", default=None,
                   metavar="NAME=CONTAINER", help="repeatable; defaults to job-search + andras")
    p.add_argument("--max-rss-mb", type=float, default=1000.0,
                   help="a tenant above this probably has an embedding model loaded")
    p.add_argument("--failure-drill", action="store_true",
                   help="STOPS the embedder briefly to prove fail-closed + self-recovery")
    p.add_argument("--compose-dir", default=".", help="dir holding the embedder compose file")
    args = p.parse_args()

    tenants: list[tuple[str, str]] = []
    for spec in (args.tenant or DEFAULT_TENANTS):
        if "=" not in spec:
            print(f"--tenant needs NAME=CONTAINER, got {spec!r}")
            return 2
        name, container = spec.split("=", 1)
        tenants.append((name, container))

    embedder = check_embedder(args.url, args.gateway_container)
    if embedder is None:
        print("\nembedder is not serving — nothing downstream can pass. Stopping here.")
        return 1
    for name, container in tenants:
        check_tenant(name, container, embedder, args.max_rss_mb)
    if args.failure_drill:
        failure_drill(args.url, args.gateway_container, tenants, args.compose_dir)

    failed = [r for r in _results if r[0] == FAIL]
    print("\n" + "=" * 58)
    print(f"{len(_results) - len(failed)}/{len(_results)} checks passed")
    for _, name, detail in failed:
        print(f"  FAILED: {name} — {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
