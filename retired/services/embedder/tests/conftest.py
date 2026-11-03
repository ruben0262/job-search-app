"""Shared test fixtures/paths."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_probe: dict[str, str | None] = {}


def _unreachable(url: str) -> str | None:
    """Return a human-readable reason the stack can't be reached, or None if it's up. Cached."""
    if url in _probe:
        return _probe[url]
    reason: str | None
    try:
        import httpx
        r = httpx.get(f"{url}/health", timeout=10)
        reason = None if r.status_code == 200 else f"/health returned HTTP {r.status_code}"
    except Exception as exc:                                    # noqa: BLE001 — any failure is one
        reason = f"{type(exc).__name__}: {exc}"
    _probe[url] = reason
    return reason


@pytest.fixture(autouse=True)
def _require_stack(request) -> None:
    """Integration modules declare a module-level URL; only those need a live stack."""
    url = os.environ.get("EMBEDDER_URL")
    if not url or getattr(request.node.module, "URL", None) is None:
        return
    reason = _unreachable(url)
    if reason:
        pytest.fail(
            f"EMBEDDER_URL={url} is set but the gateway is not reachable — {reason}\n"
            "  Check:  docker compose ps            (embedder-gateway must be Up)\n"
            "          docker compose logs embedder-gateway\n"
            "  The gateway publishes 127.0.0.1:8080 by default; if you removed the `ports:` line "
            "from docker-compose.yml, the host cannot reach it.",
            pytrace=False)
