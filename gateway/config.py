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
    model: str = os.environ.get("EMBEDDER_MODEL", "BAAI/bge-small-en-v1.5")
    dim: int = int(os.environ.get("EMBEDDER_DIM", "384"))
    normalize: bool = _bool("EMBEDDER_NORMALIZE", True)

    tei_url: str = os.environ.get("TEI_URL", "http://tei:80").rstrip("/")
    bulk_chunk: int = int(os.environ.get("EMBEDDER_BULK_CHUNK", "64"))
    tei_concurrency: int = int(os.environ.get("EMBEDDER_TEI_CONCURRENCY", "2"))
    max_texts: int = int(os.environ.get("EMBEDDER_MAX_TEXTS", "4096"))
    max_queue: int = int(os.environ.get("EMBEDDER_MAX_QUEUE", "256"))

    auth_token: str = os.environ.get("EMBEDDER_AUTH_TOKEN", "").strip()

    @property
    def version(self) -> str:
        """Stable identity of the vector space this service produces. Callers assert on it."""
        return f"{self.model}|dim={self.dim}|norm={int(self.normalize)}"

    def info(self) -> dict:
        return {"model": self.model, "dim": self.dim, "normalize": self.normalize,
                "version": self.version}


CONFIG = Config()
