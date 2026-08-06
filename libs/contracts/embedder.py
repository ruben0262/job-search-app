"""The embedder contract: client, vector serialisation, and the compatibility assert.

Port of `scorer/embedder_client.py`, which `CODE_STRUCTURE.md` §3 notes was already contract-shaped.
It lives in `libs/contracts` because both the harvester (embedding new corpus rows in bulk) and the
scorer (embedding on the serving path) speak it, and a second copy would be a second opinion about
the wire format.

**The compatibility assert is the whole point of this module.** A classifier is bound to the exact
`(model, dim)` it was trained on. If the shared embedder is serving a different vector space, the
scores are not degraded — they are meaningless, and nothing raises. So callers assert first and
refuse to serve on a mismatch (`PLATFORM_ARCHITECTURE.md` §5). The embedder's `/health` reports `ok`
only after it has probed its live backend, so this assert is checking a verified fact rather than
the embedder's own configuration file.

**Vectors are stored as raw float32 bytes**, not JSON and not pgvector. The corpus reads a vector
whole and multiplies it by a user's classifier; nothing does similarity *search*, so a blob is
sufficient and avoids an extension dependency (`PLATFORM_ARCHITECTURE.md` §4). float32 rather than
float64 halves the storage for precision no classifier here can use.
"""
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
    """What a vector was produced by. Stored per corpus row alongside the vector itself.

    Recorded rather than assumed because the embedder can be upgraded independently of the
    classifiers that consume it — and a vector from the old space next to one from the new space is
    indistinguishable by inspection.
    """

    model: str
    dim: int
    version: str


def pack_vector(values: list[float]) -> bytes:
    """Serialise a vector for `corpus.embedding`."""
    return struct.pack(f"<{len(values)}f", *values)


# How long a caller waits for the embedder before giving up. **Named, because the harvester's batch
# size is derived from it** — the two were independent numbers and the batch was sized so that a
# full one could not finish inside the timeout (ROADMAP 6ba).
DEFAULT_EMBED_TIMEOUT = 120.0


def unpack_vector(blob: bytes) -> list[float]:
    """Read a vector back. The inverse of `pack_vector`, and the only reader of that format."""
    if len(blob) % 4:
        raise EmbedderError(f"vector blob is not a whole number of float32s ({len(blob)} bytes)")
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


class EmbedderClient:
    """Talks to the shared embedder gateway. Two lanes, one contract.

    The lanes are not a nicety: the gateway runs separate worker pools for them, because priority
    alone cannot protect the fast lane — a score request still waits for an in-flight bulk call to
    return. That was measured (0.08 s idle → 8–15 s with a shared pool) and is why `/embed` and
    `/embed/bulk` are different endpoints rather than a flag.
    """

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
        """Refuse to proceed on a vector-space mismatch. Call once at startup and cache the result.

        `ok` is false until the gateway has confirmed its configuration against the live backend,
        and stays false permanently on a mismatch — so this checks something that was verified, not
        something that was declared.
        """
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
            # **A transport failure is an embedder failure, and must arrive as one.** This used to
            # propagate raw, so a caller written to handle `EmbedderError` — every caller — was
            # still killed by a read timeout leaking out of httpx. That is exactly what ended a
            # corpus rebuild on its first batch: the gateway was working, two clients were queued
            # on it, and one call went past the deadline.
            raise EmbedderError(f"{path} failed: {type(exc).__name__}: {exc}") from exc
        if response.status_code >= 400:
            # 503 is the gateway shedding load or refusing to serve an unverified vector space.
            # Either way the answer is to stop, not to substitute something plausible.
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
