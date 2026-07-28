"""Per-user model cache with hot-swap on retrain.

The single-tenant instance reloads a model by recreating the container. That does not scale to N
users on one process — and it is the reason `users.model_version` exists
(`PLATFORM_ARCHITECTURE.md` §7): the registry compares the user's current version against what it
holds and reloads only when they differ. A retrain becomes visible on the next score, with no
restart and without disturbing anyone else's cached model.

**Everything here fails closed.** A model is served only if its artifact agrees with the user's
current `profile_generation` and with the platform's `FEATURE_SCHEMA_VERSION`. Both are silent
corruptions otherwise: the estimator still predicts, the numbers still look like scores, and they
were fit against different semantics than the ones being fed in. Same discipline the embedder
applies to `(model, dim, version)`.
"""
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

from libs.contracts.profile import ProfileSchema
from libs.db.models import User, UserModel
from libs.scoring.features import FEATURE_SCHEMA_VERSION, FeatureSpec, feature_spec

logger = logging.getLogger(__name__)

# Small on purpose. A classifier over 768-dim vectors is a few MB, so 32 users resident is tens of
# megabytes — but the number is a knob rather than a guess, because a platform that grows past it
# should evict rather than grow unboundedly.
DEFAULT_CAPACITY = 32


class ModelUnavailable(RuntimeError):
    """No servable model for this user. Never an error the caller should paper over.

    Carries `reason` so the caller can distinguish "not trained yet" (normal for a new account,
    cold start covers it) from "artifact is stale" (a retrain is owed).
    """

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


def resolve_artifact(path: str) -> str:
    """Where the artifact actually is, now, on this host.

    **The stored path is a record of where it was written, not where to find it.** `models.path`
    holds an absolute path from whichever process trained the model — and that process was a script
    on the host, while the reader is a container with the artifacts mounted somewhere else
    entirely. The same mismatch breaks a restore onto a new machine.

    So the *filename* is the identity and `MODEL_DIR` is the location. The stored path is used only
    when the file is genuinely there, which keeps a hand-placed artifact working.
    """
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
        """This user's current model, loading or reloading as needed. Raises `ModelUnavailable`.

        The version check is a single indexed read per call — deliberately cheaper than the reload
        it prevents, so it can run on every score rather than on a timer. A timer would mean a
        window where a just-retrained model is not yet serving and nothing says so.
        """
        row = self._s.execute(
            select(User.model_version, User.profile_generation)
            .where(User.id == user_id)).first()
        if row is None:
            raise ModelUnavailable(f"no such user: {user_id}")
        model_version, profile_generation = row

        cached = self._cache.get(user_id)
        if cached is not None and cached.model_version == model_version:
            if cached.profile_generation != profile_generation:
                # The profile changed under a model that was current a moment ago. The artifact is
                # not corrupt, it is answering an older question.
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
            # `users.model_version` moved but the row is missing: an interrupted trainer, or a
            # pruned artifact. Refusing is right — serving the previous version would silently
            # answer with a model the user's own dashboard says is superseded.
            raise ModelUnavailable(
                f"user is on model_version {model_version} but no artifact row exists",
                retrainable=True)

        if artifact_row.profile_generation != profile_generation:
            raise ModelUnavailable(
                f"artifact was trained against profile generation "
                f"{artifact_row.profile_generation}, user is now on {profile_generation}",
                retrainable=True)
        if artifact_row.feature_schema_version != FEATURE_SCHEMA_VERSION:
            # The columns still line up by count, so nothing would crash — the weights would just
            # be attached to features that no longer mean what they meant.
            raise ModelUnavailable(
                f"artifact uses feature schema {artifact_row.feature_schema_version}, "
                f"this build is {FEATURE_SCHEMA_VERSION}", retrainable=True)

        profile = self._s.scalar(select(User.profile).where(User.id == user_id))
        if not profile:
            raise ModelUnavailable("user has no confirmed profile", retrainable=False)

        blob = self._load(artifact_row.path)
        cutoff = int(blob.get("decision_cutoff", blob.get("score_threshold", 60)))
        return LoadedModel(
            user_id=user_id,
            model_version=model_version,
            profile_generation=profile_generation,
            estimator=blob["model"],
            task=blob.get("task", "regress"),
            features=feature_spec(ProfileSchema.model_validate(profile)),
            embedder_model=artifact_row.embedder_model,
            embedder_dim=artifact_row.embedder_dim,
            embedder_version=artifact_row.embedder_version,
            cutoff=cutoff,
            keep_cutoff=int(blob.get("keep_cutoff", max(cutoff - 10, 1))))
