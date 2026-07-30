"""Fit one user's classifier and publish it atomically.

Two things make this different from the single-tenant trainer:

1. **The training set is scoped by `profile_generation`.** A label written under an older rubric is
   not merely old — it is an answer to a different question, and mixing generations trains a model
   on two contradictory definitions of "good match" (`PROFILE_CAPTURE_PLAN.md` §6.4).
2. **The artifact row and the `model_version` bump commit together.** The scorer decides what to
   serve from `users.model_version`; if that moved without its artifact row, every score for that
   user fails until someone notices. One transaction makes the half-state unrepresentable.

Embeddings are read from the shared corpus, not recomputed — the expensive part of a retrain is
already paid once for everyone during harvest, which is what makes a per-user retrain seconds rather
than minutes (`HARVESTER_PLAN.md` §7).
"""
from __future__ import annotations

import logging
import os
import tempfile
import uuid as _uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

import libs.notify as notifylib
from libs.contracts.embedder import unpack_vector
from libs.contracts.profile import ProfileSchema
from libs.db.models import CorpusJob, User, UserLabel, UserModel
from libs.scoring.features import FEATURE_SCHEMA_VERSION, feature_spec, job_features

logger = logging.getLogger(__name__)

# A model refit on one extra label is not worth the CPU (`SCHEDULER_PLAN.md` §7). This is the floor
# below which a retrain is skipped rather than queued and immediately abandoned.
MIN_LABELS = 25
MIN_NEW_LABELS = 5

# The band boundaries the scorer reads back out of the artifact.
DEFAULT_CUTOFF = 60


class NotEnoughData(RuntimeError):
    """Not enough labels at the current generation to fit anything meaningful. Not a failure."""


@dataclass(frozen=True)
class TrainingSet:
    job_ids: list[int]
    matrix: Any            # numpy array: embedding || features
    targets: list[float]
    weights: list[float]
    embedder_model: str
    embedder_dim: int
    embedder_version: str


def build_training_set(session: Session, user_id: _uuid.UUID) -> TrainingSet:
    """This user's labels at their **current** generation, joined to corpus vectors.

    Rows whose corpus entry has no embedding yet are dropped rather than zero-filled: a zero vector
    is a confident statement that the posting means nothing, and the model would learn it.
    """
    import numpy as np  # noqa: PLC0415 - the trainer image has numpy; libs importers need not

    user = session.get(User, user_id)
    if user is None or not user.profile:
        raise NotEnoughData("user has no confirmed profile")

    rows = session.execute(
        select(UserLabel.job_id, UserLabel.label, UserLabel.weight,
               CorpusJob.title, CorpusJob.description,
               CorpusJob.embedding, CorpusJob.embedding_model,
               CorpusJob.embedding_dim, CorpusJob.embedding_version)
        .join(CorpusJob, CorpusJob.id == UserLabel.job_id)
        .where(UserLabel.user_id == user_id,
               UserLabel.profile_generation == user.profile_generation,
               CorpusJob.embedding.is_not(None))).all()

    if len(rows) < MIN_LABELS:
        raise NotEnoughData(
            f"{len(rows)} usable labels at generation {user.profile_generation}, need {MIN_LABELS}")

    # One embedder per training set, always. A mixture of vector spaces produces a model that is
    # wrong in a way no metric reveals — the holdout is drawn from the same mixture.
    versions = {r.embedding_version for r in rows}
    if len(versions) > 1:
        raise NotEnoughData(
            f"labels span {len(versions)} embedder versions: {sorted(versions)}. "
            f"Re-embed the corpus before retraining.")

    spec = feature_spec(ProfileSchema.model_validate(user.profile))
    vectors = [unpack_vector(r.embedding) for r in rows]
    features = [job_features(spec, r.title or "", r.description or "") for r in rows]

    return TrainingSet(
        job_ids=[r.job_id for r in rows],
        matrix=np.hstack([np.array(vectors, dtype=np.float32),
                          np.array(features, dtype=np.float32)]),
        targets=[float(r.label) for r in rows],
        weights=[float(r.weight) for r in rows],
        embedder_model=rows[0].embedding_model,
        embedder_dim=rows[0].embedding_dim,
        embedder_version=rows[0].embedding_version)


def _default_fit(data: TrainingSet) -> dict:
    """Ridge regression over embedding + features. Deliberately unremarkable.

    The model class is not where the quality is — the rubric and the labels are. A linear head over
    a good embedding is what the reference instance settled on, and it trains in seconds on a CPU
    the notifier also has to share.
    """
    from sklearn.linear_model import Ridge  # noqa: PLC0415 - only the trainer image has sklearn

    estimator = Ridge(alpha=1.0)
    estimator.fit(data.matrix, data.targets, sample_weight=data.weights)
    return {"model": estimator, "task": "regress", "model_type": "ridge"}


def train_user(session: Session, user_id: _uuid.UUID, *,
               artifact_dir: Path,
               fit: Callable[[TrainingSet], dict] = _default_fit,
               cutoff: int = DEFAULT_CUTOFF) -> int:
    """Fit, publish, and return the new `model_version`.

    Publishing is one transaction the caller commits: artifact row, `is_active` flip, and the
    version bump. The queue job's own completion belongs in the same commit
    (`SCHEDULER_PLAN.md` §3) — that is what makes "queue says done, database says not" impossible.
    """
    user = session.get(User, user_id)
    if user is None:
        raise NotEnoughData(f"no such user: {user_id}")

    data = build_training_set(session, user_id)
    blob = dict(fit(data))
    blob.setdefault("task", "regress")
    blob["decision_cutoff"] = cutoff
    blob["keep_cutoff"] = max(cutoff - 15, 1)
    blob["n_train"] = len(data.targets)

    version = (user.model_version or 0) + 1
    path = _write_artifact(artifact_dir, user_id, version, blob)

    # Exactly one active artifact per user is a partial unique index, so the previous one has to be
    # cleared in the same statement batch rather than trusted to be cleared later.
    session.query(UserModel).filter(
        UserModel.user_id == user_id, UserModel.is_active.is_(True)).update({"is_active": False})
    session.add(UserModel(
        user_id=user_id, version=version, path=str(path),
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        profile_generation=user.profile_generation,
        embedder_model=data.embedder_model, embedder_dim=data.embedder_dim,
        embedder_version=data.embedder_version,
        n_labels=len(data.targets), is_active=True,
        metrics={"n_train": len(data.targets)}))
    user.model_version = version
    notifylib.notify(session, user_id, notifylib.MODEL_TRAINED,
                     f"Version {version}, trained on {len(data.targets)} of your decisions. "
                     "Your feed is ranked by it from the next cycle.")
    return version


def should_retrain(session: Session, user_id: _uuid.UUID, *,
                   min_new: int = MIN_NEW_LABELS) -> bool:
    """Whether enough has changed since the active artifact to be worth the CPU.

    The queue's `dedup_key` collapses a burst into one job; this is the other half — a job that
    fires anyway when only one label arrived would spend a training slot on nothing.
    """
    active = session.scalar(
        select(UserModel).where(UserModel.user_id == user_id, UserModel.is_active.is_(True)))
    labelled = session.scalar(
        select(func.count()).select_from(UserLabel).where(UserLabel.user_id == user_id)) or 0
    if active is None:
        return labelled >= MIN_LABELS
    return labelled - (active.n_labels or 0) >= min_new


def _write_artifact(artifact_dir: Path, user_id: _uuid.UUID, version: int, blob: dict) -> Path:
    """Temp file then rename. A half-written artifact must never be loadable.

    The rename is atomic within a filesystem, so the scorer either sees the previous file or the
    complete new one — never a truncated joblib that fails at load time, on a user's next score.
    """
    import joblib  # noqa: PLC0415 - only the trainer image has joblib

    artifact_dir = Path(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    final = artifact_dir / f"{user_id}.v{version}.joblib"

    handle, temp_name = tempfile.mkstemp(dir=artifact_dir, suffix=".tmp")
    os.close(handle)
    temp = Path(temp_name)
    try:
        joblib.dump(blob, temp)
        temp.replace(final)
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    return final
