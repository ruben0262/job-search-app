"""Environment-driven config + the version string callers assert against.

The embedder VERSION is the contract: any classifier was trained against a specific
(model, dim, normalize) triple. Callers read /health, compare their artifact's expected
values, and refuse to score on a mismatch (PLAN §13). Bump nothing here casually — a change
that alters output vectors invalidates every attached classifier.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    model: str = os.environ.get("EMBEDDER_MODEL", "BAAI/bge-base-en-v1.5")
    dim: int = int(os.environ.get("EMBEDDER_DIM", "768"))
    normalize: bool = _bool("EMBEDDER_NORMALIZE", True)

    tei_url: str = os.environ.get("TEI_URL", "http://tei:80").rstrip("/")
    # HARD cap on texts per TEI call. TEI rejects a request over its own --max-client-batch-size
    # (default 32), so this MUST be <= that flag or every oversized batch 413s. Single source of
    # truth: docker-compose passes the same env to both sides, and verify.py asserts agreement
    # against the live backend at startup.
    tei_batch: int = int(os.environ.get("TEI_MAX_CLIENT_BATCH", "32"))
    # Bulk chunk. Default 8 = the ONNX CPU backend's own max batch: TEI splits anything larger into
    # 8s internally, so a bigger chunk buys NO inference speed and only makes a worker (and TEI's
    # queue head) block ~N/8x longer — which is what starves the score lane.
    bulk_chunk: int = int(os.environ.get("EMBEDDER_BULK_CHUNK", "8"))
    # Separate worker pools per lane. A shared pool cannot protect the fast lane: priority orders
    # the QUEUE, but a score request still waits for an in-flight bulk call to return. Reserving
    # workers is what actually makes the fast lane fast (PLAN §4c/§14.8).
    score_workers: int = int(os.environ.get("EMBEDDER_SCORE_WORKERS", "2"))
    bulk_workers: int = int(os.environ.get("EMBEDDER_BULK_WORKERS", "1"))
    max_texts: int = int(os.environ.get("EMBEDDER_MAX_TEXTS", "4096"))
    max_queue: int = int(os.environ.get("EMBEDDER_MAX_QUEUE", "256"))

    auth_token: str = os.environ.get("EMBEDDER_AUTH_TOKEN", "").strip()

    @property
    def chunk_bulk(self) -> int:
        """Bulk lane chunk, never above what TEI will accept in one call."""
        return max(1, min(self.bulk_chunk, self.tei_batch))

    @property
    def chunk_score(self) -> int:
        """Fast lane chunk. Same TEI ceiling — a caller scoring 100 postings in one POST must not
        be forwarded as one oversized batch."""
        return max(1, self.tei_batch)

    @property
    def version(self) -> str:
        """Stable identity of the vector space this service produces. Callers assert on it."""
        return f"{self.model}|dim={self.dim}|norm={int(self.normalize)}"

    def info(self) -> dict:
        return {"model": self.model, "dim": self.dim, "normalize": self.normalize,
                "version": self.version}


CONFIG = Config()
