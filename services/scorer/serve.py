"""Scoring a batch of corpus rows for one user.

The structural difference from the single-tenant instance: **nothing is embedded here.** The
corpus row already carries its vector, computed once for everyone during harvest, so scoring N
users over the same posting is N cheap predictions rather than N embeddings
(`PLATFORM_ARCHITECTURE.md` §4). That is the whole economic argument for the shared corpus, and it
only holds if this path never falls back to embedding on its own.

Which makes the version check load-bearing: a row embedded by a different model, or at a different
dimension, cannot be fed to an estimator fit against the first. It is refused rather than scored,
the same fail-closed rule the embedder applies.
"""
from __future__ import annotations

from dataclasses import dataclass

from libs.contracts.embedder import unpack_vector
from libs.scoring.features import job_features
from services.scorer.registry import LoadedModel

# A description shorter than this is a stub — a redirect page, a "sign in to view", a truncated
# feed item. Scored anyway, but flagged, because the score means much less than it looks like it
# does and the caller may want to fetch the full text first.
MIN_DESCRIPTION = 100


@dataclass(frozen=True)
class Scored:
    job_id: int
    score: int
    decision: str          # "high" | "needs-review" | "skip"
    thin: bool             # description too short for the score to mean much


class EmbeddingMismatch(RuntimeError):
    """A corpus row's vector was not produced by the embedder this model was trained against."""


def score_rows(model: LoadedModel, rows: list[dict]) -> list[Scored]:
    """Score corpus rows. Each row needs `id`, `title`, `description`, `embedding`, `embedding_dim`.

    Rows without an embedding are skipped rather than scored — they are not yet ready, which is a
    normal state between harvest and the embed pass, not an error.
    """
    import numpy as np  # noqa: PLC0415 - the scorer image has numpy; libs importers need not

    ready = [r for r in rows if r.get("embedding")]
    if not ready:
        return []

    vectors = []
    for row in ready:
        # Version before dimension: two models can share a dimension and produce vectors that mean
        # entirely different things, so a matching width proves nothing on its own.
        version = row.get("embedding_version")
        if version is not None and version != model.embedder_version:
            raise EmbeddingMismatch(
                f"job {row['id']} was embedded by {version!r}, model was trained against "
                f"{model.embedder_version!r}")
        dim = row.get("embedding_dim") or model.embedder_dim
        if dim != model.embedder_dim:
            raise EmbeddingMismatch(
                f"job {row['id']} is embedded at dim {dim}, model expects {model.embedder_dim}")
        vectors.append(unpack_vector(row["embedding"]))

    features = [job_features(model.features, r.get("title") or "", r.get("description") or "")
                for r in ready]
    matrix = np.hstack([np.array(vectors, dtype=np.float32),
                        np.array(features, dtype=np.float32)])

    if model.task == "classify":
        # Probability of "fit", scaled so the cutoff is directly comparable to a regressed score.
        predictions = model.estimator.predict_proba(matrix)[:, 1] * 100.0
    else:
        predictions = np.clip(model.estimator.predict(matrix), 0, 100)

    keep = min(model.keep_cutoff, model.cutoff - 1) if model.cutoff > 1 else model.keep_cutoff
    return [
        Scored(job_id=row["id"],
               score=(score := round(float(prediction))),
               decision=_decide(score, model.cutoff, keep),
               thin=len(row.get("description") or "") < MIN_DESCRIPTION)
        for row, prediction in zip(ready, predictions, strict=True)
    ]


def _decide(score: int, cutoff: int, keep: int) -> str:
    if score >= cutoff:
        return "high"
    if score >= keep:
        return "needs-review"
    return "skip"
