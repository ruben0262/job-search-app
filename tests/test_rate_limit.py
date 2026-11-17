"""The rate limiter, as the site meets its visitors: through Cloudflare's tunnel, where every request arrives
from the tunnel's own address and the visitor's is in Cloudflare's header."""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from services.api.deps import rate_limit

# The tunnel's container, the peer of every request that reaches the API on the server.
TUNNEL = ("172.18.0.5", 41234)


def _from(visitor: str) -> Request:
    """A request as the tunnel hands it on: its own address as the peer, the visitor's in Cloudflare's
    header."""
    return Request({"type": "http", "method": "POST", "path": "/signup", "client": TUNNEL,
                    "headers": [(b"cf-connecting-ip", visitor.encode())]})


def test_each_visitor_is_counted_apart_behind_the_tunnel(monkeypatch):
    """Keyed on the peer, every visitor shared the tunnel's one count: five sign-ups an hour for the whole
    site, and a sixth person was told to wait (found 2026-11-17)."""
    monkeypatch.delenv("DISABLE_RATE_LIMIT", raising=False)
    key = f"test-{uuid.uuid4()}"
    for _ in range(2):
        rate_limit(_from("203.0.113.7"), key, limit=2, per_seconds=60)

    with pytest.raises(HTTPException) as refused:
        rate_limit(_from("203.0.113.7"), key, limit=2, per_seconds=60)
    assert refused.value.status_code == 429
    rate_limit(_from("198.51.100.20"), key, limit=2, per_seconds=60)  # somebody else: not refused
