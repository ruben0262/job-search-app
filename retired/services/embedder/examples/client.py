"""Reference caller — how a scorer deployment integrates the shared embedder."""
from __future__ import annotations

import httpx


class EmbedderError(RuntimeError):
    pass


class IncompatibleEmbedder(EmbedderError):
    """The shared embedder's vector space != what this classifier was trained on."""


class EmbedderClient:
    def __init__(self, base_url: str = "http://embedder-gateway:8080",
                 token: str | None = None, timeout: float = 120.0) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout)

    def health(self) -> dict:
        r = self._client.get("/health")
        r.raise_for_status()
        return r.json()

    def assert_compatible(self, expected_model: str, expected_dim: int) -> None:
        """Refuse to score on a vector-space mismatch. Call once at startup (and cache the ok)."""
        h = self.health()
        if not h.get("ok"):
            # `ok` is false until the gateway confirms its config; `verify_detail` says why.
            raise EmbedderError(
                f"embedder not healthy: {h.get('verify_detail') or h}")
        if h.get("model") != expected_model or int(h.get("dim", -1)) != int(expected_dim):
            raise IncompatibleEmbedder(
                f"artifact expects model={expected_model} dim={expected_dim}, "
                f"embedder serves model={h.get('model')} dim={h.get('dim')} — retrain the "
                f"classifier on the shared model or point at the right embedder (PLAN §10/§13)")

    def embed_score(self, texts: list[str]) -> list[list[float]]:
        """Fast lane — for on-arrival scoring where latency matters."""
        return self._post("/embed", texts)

    def embed_bulk(self, texts: list[str]) -> list[list[float]]:
        """Bulk lane — for training-corpus embeds where throughput matters."""
        return self._post("/embed/bulk", texts)

    def _post(self, path: str, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        r = self._client.post(path, json={"texts": texts})
        r.raise_for_status()
        return r.json()["vectors"]

    def close(self) -> None:
        self._client.close()


if __name__ == "__main__":
    # Smoke test against a running stack (EMBEDDER_URL / EMBEDDER_TOKEN in env).
    import os

    c = EmbedderClient(os.environ.get("EMBEDDER_URL", "http://localhost:8080"),
                       os.environ.get("EMBEDDER_TOKEN") or None)
    print("health:", c.health())
    vecs = c.embed_score(["backend engineer, Go and Postgres, fully remote EU"])
    print("dim:", len(vecs[0]))
