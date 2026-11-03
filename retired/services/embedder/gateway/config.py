"""Environment-driven config + the version string callers assert against."""
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
    # HARD cap on texts per TEI call.
    tei_batch: int = int(os.environ.get("TEI_MAX_CLIENT_BATCH", "32"))
    # Bulk chunk.
    bulk_chunk: int = int(os.environ.get("EMBEDDER_BULK_CHUNK", "8"))
    # Separate worker pools per lane.
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
        """Fast lane chunk."""
        return max(1, self.tei_batch)

    @property
    def version(self) -> str:
        """Stable identity of the vector space this service produces. Callers assert on it."""
        return f"{self.model}|dim={self.dim}|norm={int(self.normalize)}"

    def info(self) -> dict:
        return {"model": self.model, "dim": self.dim, "normalize": self.normalize,
                "version": self.version}


CONFIG = Config()
