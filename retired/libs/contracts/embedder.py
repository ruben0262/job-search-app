"""The embedder contract: client, vector serialisation, and the compatibility assert."""
from __future__ import annotations

import struct
from dataclasses import dataclass

import httpx


class EmbedderError(RuntimeError):
    """The embedder could not be used. Always fail closed — never fall back to a guess."""


class IncompatibleEmbedder(EmbedderError):
    """The shared embedder's vector space is not the one this classifier was trained on."""


@dataclass(frozen=True)
class EmbedderVersion:
    """What a vector was produced by."""

    model: str
    dim: int
    version: str


def pack_vector(values: list[float]) -> bytes:
    """Serialise a vector for `corpus.embedding`."""
    return struct.pack(f"<{len(values)}f", *values)


# How long a caller waits for the embedder before giving up.
DEFAULT_EMBED_TIMEOUT = 120.0


def unpack_vector(blob: bytes) -> list[float]:
    """Read a vector back. The inverse of `pack_vector`, and the only reader of that format."""
    if len(blob) % 4:
        raise EmbedderError(f"vector blob is not a whole number of float32s ({len(blob)} bytes)")
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


class EmbedderClient:
    """Talks to the shared embedder gateway."""

    def __init__(self, base_url: str = "http://embedder-gateway:8080",
                 token: str | None = None, timeout: float = DEFAULT_EMBED_TIMEOUT,
                 client: httpx.Client | None = None) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        # `client` is injectable so tests can drive a stub transport. Not for production use.
        self._client = client or httpx.Client(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout, trust_env=False)

    def health(self) -> dict:
        response = self._client.get("/health")
        response.raise_for_status()
        return response.json()

    def version(self) -> EmbedderVersion:
        """What the backend is actually serving, confirmed against a live probe."""
        health = self.health()
        if not health.get("ok"):
            raise EmbedderError(
                f"embedder not healthy: {health.get('verify_detail') or health}")
        return EmbedderVersion(
            model=str(health.get("model")), dim=int(health.get("dim", -1)),
            version=str(health.get("version")))

    def assert_compatible(self, expected: EmbedderVersion) -> None:
        """Refuse to proceed on a vector-space mismatch."""
        actual = self.version()
        if actual.model != expected.model or actual.dim != expected.dim:
            raise IncompatibleEmbedder(
                f"artifact expects model={expected.model} dim={expected.dim}, embedder serves "
                f"model={actual.model} dim={actual.dim} — retrain on the shared model or point at "
                f"the right embedder")

    def embed_score(self, texts: list[str]) -> list[list[float]]:
        """Fast lane. On-arrival scoring, where latency is the constraint."""
        return self._post("/embed", texts)

    def embed_bulk(self, texts: list[str]) -> list[list[float]]:
        """Bulk lane. Harvest and training embeds, where throughput is the constraint."""
        return self._post("/embed/bulk", texts)

    def _post(self, path: str, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = self._client.post(path, json={"texts": texts})
        except httpx.HTTPError as exc:
            # A transport failure is an embedder failure, and must arrive as one.
            raise EmbedderError(f"{path} failed: {type(exc).__name__}: {exc}") from exc
        if response.status_code >= 400:
            # 503 is the gateway shedding load or refusing to serve an unverified vector space.
            raise EmbedderError(f"{path} returned HTTP {response.status_code}: {response.text[:300]}")
        vectors = response.json().get("vectors")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise EmbedderError(
                f"{path} returned {len(vectors) if isinstance(vectors, list) else '?'} vectors "
                f"for {len(texts)} texts — refusing to guess which is which")
        return vectors

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> EmbedderClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
