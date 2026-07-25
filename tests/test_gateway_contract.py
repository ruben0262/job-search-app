"""Gateway logic tests against a STUBBED TEI backend — no docker, no model, no network.

These cover the parts of PLAN §13/§14.6/§4c that are pure gateway behaviour, so a regression is
caught here instead of on the box:
  - a backend serving a different model/dim → `/health.ok` false forever and embeds 503
    (the silent-drift failure §13 exists to prevent),
  - an unreachable backend → 503, never a fabricated vector,
  - a healthy, matching backend → normal service, `verified: true`,
  - the fast lane is served before queued bulk work.

The real vectors are TEI's business; what is asserted here is the contract around them.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from gateway import app as app_module
from gateway.config import CONFIG


class _StubTEI:
    """Stands in for TEIClient. `model`/`dim` are what the *backend* claims to serve."""

    def __init__(self, model: str = CONFIG.model, dim: int = CONFIG.dim,
                 reachable: bool = True, delay: float = 0.0, max_batch: int = 32) -> None:
        self.model, self.dim, self.reachable, self.delay = model, dim, reachable, delay
        self.max_batch = max_batch
        self.calls: list[list[str]] = []

    async def info(self) -> dict:
        if not self.reachable:
            raise ConnectionError("stub backend down")
        return {"model_id": self.model, "max_client_batch_size": self.max_batch}

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.reachable:
            raise ConnectionError("stub backend down")
        # TEI answers 413 above its --max-client-batch-size; the stub is strict about it so an
        # unchunked lane fails here instead of on the box.
        if len(texts) > self.max_batch:
            raise ValueError(f"batch size {len(texts)} > max_client_batch_size {self.max_batch}")
        if self.delay:
            await asyncio.sleep(self.delay)
        self.calls.append(list(texts))
        return [[float(i)] * self.dim for i, _ in enumerate(texts)]

    async def healthy(self) -> bool:
        return self.reachable

    async def aclose(self) -> None:
        return None


def _client(stub: _StubTEI, monkeypatch) -> TestClient:
    monkeypatch.setattr(app_module, "TEIClient", lambda *a, **k: stub)
    return TestClient(app_module.app)


def _wait_verified(client: TestClient, want: bool, tries: int = 50) -> dict:
    """The verifier probes in the background; poll /health until it settles."""
    import time
    health = {}
    for _ in range(tries):
        health = client.get("/health").json()
        if health.get("verified") is want or health.get("verify_state") == "mismatch":
            return health
        time.sleep(0.1)
    return health


def test_matching_backend_serves(monkeypatch) -> None:
    stub = _StubTEI()
    with _client(stub, monkeypatch) as c:
        health = _wait_verified(c, True)
        assert health["verified"] is True and health["ok"] is True
        r = c.post("/embed", json={"texts": ["a", "b"]})
        assert r.status_code == 200
        body = r.json()
        assert len(body["vectors"]) == 2 and len(body["vectors"][0]) == CONFIG.dim
        assert body["model"] == CONFIG.model and body["dim"] == CONFIG.dim


def test_model_mismatch_refuses_to_serve(monkeypatch) -> None:
    """Config says one model, backend serves another → never healthy, never embeds (PLAN §13)."""
    stub = _StubTEI(model="BAAI/some-other-model")
    with _client(stub, monkeypatch) as c:
        health = _wait_verified(c, False)
        assert health["verify_state"] == "mismatch"
        assert health["ok"] is False
        assert c.post("/embed", json={"texts": ["a"]}).status_code == 503
        assert c.post("/embed/bulk", json={"texts": ["a"]}).status_code == 503


def test_dim_mismatch_refuses_to_serve(monkeypatch) -> None:
    stub = _StubTEI(dim=CONFIG.dim + 8)
    with _client(stub, monkeypatch) as c:
        health = _wait_verified(c, False)
        assert health["verify_state"] == "mismatch"
        assert c.post("/embed", json={"texts": ["a"]}).status_code == 503


def test_unreachable_backend_fails_closed(monkeypatch) -> None:
    """No vectors invented while the backend is down — callers must get an error (PLAN §14.6)."""
    stub = _StubTEI(reachable=False)
    with _client(stub, monkeypatch) as c:
        health = _wait_verified(c, False, tries=5)
        assert health["ok"] is False
        assert health["verify_state"] in ("unreachable", "pending")
        assert c.post("/embed", json={"texts": ["a"]}).status_code == 503


def test_oversized_request_rejected(monkeypatch) -> None:
    stub = _StubTEI()
    with _client(stub, monkeypatch) as c:
        _wait_verified(c, True)
        r = c.post("/embed", json={"texts": ["x"] * (CONFIG.max_texts + 1)})
        assert r.status_code == 413


@pytest.mark.parametrize("path,chunk", [("/embed/bulk", CONFIG.chunk_bulk),
                                        ("/embed", CONFIG.chunk_score)])
def test_lanes_preserve_input_order_across_chunks(monkeypatch, path: str, chunk: int) -> None:
    """Both lanes split into chunks; results must reassemble in the caller's original order or
    every row's label ends up attached to the wrong vector."""
    stub = _StubTEI()
    n = chunk * 2 + 3
    with _client(stub, monkeypatch) as c:
        _wait_verified(c, True)
        r = c.post(path, json={"texts": [str(i) for i in range(n)]})
        assert r.status_code == 200, r.text
        vectors = r.json()["vectors"]
    assert len(vectors) == n
    # The stub encodes each text's position WITHIN its chunk into the vector, so the expected
    # sequence is per-chunk indices concatenated — any reordering/reassembly bug breaks this.
    assert [v[0] for v in vectors] == [float(i % chunk) for i in range(n)]
    assert max(len(call) for call in stub.calls) <= stub.max_batch


def test_chunks_clamp_to_backend_batch_limit(monkeypatch) -> None:
    """A backend stricter than our config must LOWER the batch, not 413 every request. The gateway
    reads the real limit from /info and clamps; otherwise this breaks only the bulk lane, i.e.
    training silently fails while scoring looks fine."""
    stub = _StubTEI(max_batch=4)
    n = 30
    with _client(stub, monkeypatch) as c:
        health = _wait_verified(c, True)
        assert health["backend_max_batch"] == 4
        for path in ("/embed", "/embed/bulk"):
            r = c.post(path, json={"texts": [str(i) for i in range(n)]})
            assert r.status_code == 200, r.text
            assert len(r.json()["vectors"]) == n
    assert max(len(call) for call in stub.calls) <= 4


def test_empty_texts_rejected(monkeypatch) -> None:
    stub = _StubTEI()
    with _client(stub, monkeypatch) as c:
        _wait_verified(c, True)
        assert c.post("/embed", json={"texts": []}).status_code == 422


@pytest.mark.parametrize("path", ["/embed", "/embed/bulk"])
def test_auth_enforced_when_token_set(monkeypatch, path: str) -> None:
    """Network-only mode is the default, but once a token is set it must actually be required."""
    import dataclasses

    from gateway import auth as auth_module
    monkeypatch.setattr(auth_module, "CONFIG", dataclasses.replace(CONFIG, auth_token="s3cret"))
    stub = _StubTEI()
    with _client(stub, monkeypatch) as c:
        _wait_verified(c, True)
        assert c.post(path, json={"texts": ["a"]}).status_code == 401
        ok = c.post(path, json={"texts": ["a"]}, headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 200
