"""Per-user model cache with hot-swap on retrain."""
from __future__ import annotations

import logging
import os
import uuid as _uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from libs.db.models import User, UserModel
from libs.profiles.repository import ProfileFiles
from libs.scoring.features import FEATURE_SCHEMA_VERSION, FeatureSpec, spec_for

logger = logging.getLogger(__name__)

# Small on purpose.
DEFAULT_CAPACITY = 32


class ModelUnavailable(RuntimeError):
    """No servable model for this user."""

    def __init__(self, reason: str, *, retrainable: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.retrainable = retrainable


@dataclass(frozen=True)
class LoadedModel:
    """One user's servable artifact, plus everything needed to check it still applies."""

    user_id: _uuid.UUID
    model_version: int
    profile_generation: int
    estimator: Any
    task: str                 # "classify" -> predict_proba * 100; otherwise predict
    features: FeatureSpec
    embedder_model: str
    embedder_dim: int
    embedder_version: str
    cutoff: int
    keep_cutoff: int
    # The pre-score filter measured from the labels this artifact was fitted on.
    centroid: bytes | None = None
    centroid_threshold: float | None = None


def resolve_artifact(path: str) -> str:
    """Where the artifact actually is, now, on this host."""
    if os.path.exists(path):
        return path
    return os.path.join(os.environ.get("MODEL_DIR", "/var/lib/jobsearch/models"),
                        os.path.basename(path))


def _default_loader(path: str) -> dict:
    import joblib  # noqa: PLC0415 - keeps the registry importable without sklearn/joblib

    return joblib.load(resolve_artifact(path))


class ModelRegistry:
    """LRU of per-user models, refreshed when `users.model_version` moves."""

    def __init__(self, session: Session, *, capacity: int = DEFAULT_CAPACITY,
                 load_artifact: Callable[[str], dict] = _default_loader):
        self._s = session
        self._capacity = capacity
        self._load = load_artifact
        self._cache: OrderedDict[_uuid.UUID, LoadedModel] = OrderedDict()

    def get(self, user_id: _uuid.UUID) -> LoadedModel:
        """This user's current model, loading or reloading as needed."""
        row = self._s.execute(
            select(User.model_version, User.profile_generation)
            .where(User.id == user_id)).first()
        if row is None:
            raise ModelUnavailable(f"no such user: {user_id}")
        model_version, profile_generation = row

        cached = self._cache.get(user_id)
        if cached is not None and cached.model_version == model_version:
            if cached.profile_generation != profile_generation:
                # The profile changed under a model that was current a moment ago.
                self._cache.pop(user_id, None)
                raise ModelUnavailable(
                    f"model was trained against profile generation {cached.profile_generation}, "
                    f"user is now on {profile_generation}", retrainable=True)
            self._cache.move_to_end(user_id)
            return cached

        loaded = self._load_for(user_id, model_version, profile_generation)
        self._cache[user_id] = loaded
        self._cache.move_to_end(user_id)
        while len(self._cache) > self._capacity:
            evicted, _ = self._cache.popitem(last=False)
            logger.debug("scorer: evicted model for %s", evicted)
        return loaded

    def invalidate(self, user_id: _uuid.UUID) -> None:
        """Drop a cached model. For the trainer, and for tests; not needed in the normal path."""
        self._cache.pop(user_id, None)

    @property
    def resident(self) -> int:
        return len(self._cache)

    # ── loading ──────────────────────────────────────────────────────────────────────────────

    def _load_for(self, user_id: _uuid.UUID, model_version: int,
                  profile_generation: int) -> LoadedModel:
        if model_version <= 0:
            raise ModelUnavailable("no model trained yet", retrainable=True)

        artifact_row = self._s.scalar(
            select(UserModel)
            .where(UserModel.user_id == user_id, UserModel.version == model_version))
        if artifact_row is None:
            # `users.model_version` moved but the row is missing: an interrupted trainer or a pruned artifact.
            raise ModelUnavailable(
                f"user is on model_version {model_version} but no artifact row exists",
                retrainable=True)

        if artifact_row.profile_generation != profile_generation:
            raise ModelUnavailable(
                f"artifact was trained against profile generation "
                f"{artifact_row.profile_generation}, user is now on {profile_generation}",
                retrainable=True)
        if artifact_row.feature_schema_version != FEATURE_SCHEMA_VERSION:
            # The columns still line up by count, so weights would attach to features that changed meaning.
            raise ModelUnavailable(
                f"artifact uses feature schema {artifact_row.feature_schema_version}, "
                f"this build is {FEATURE_SCHEMA_VERSION}", retrainable=True)

        # Either flow: the capture step's JSON, or the profile files the conversation writes.
        profile = self._s.scalar(select(User.profile).where(User.id == user_id))
        if not profile and ProfileFiles(self._s, user_id).missing_setup():
            raise ModelUnavailable("user has no finished profile", retrainable=False)

        blob = self._load(artifact_row.path)
        cutoff = int(blob.get("decision_cutoff", blob.get("score_threshold", 60)))
        return LoadedModel(
            user_id=user_id,
            model_version=model_version,
            profile_generation=profile_generation,
            estimator=blob["model"],
            task=blob.get("task", "regress"),
            features=spec_for(profile),
            embedder_model=artifact_row.embedder_model,
            embedder_dim=artifact_row.embedder_dim,
            embedder_version=artifact_row.embedder_version,
            cutoff=cutoff,
            keep_cutoff=int(blob.get("keep_cutoff", max(cutoff - 10, 1))),
            # From the row rather than the artifact: the centroid is a property of the labels.
            centroid=artifact_row.centroid,
            centroid_threshold=artifact_row.centroid_threshold)
