"""Fit one user's classifier and publish it atomically."""
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
from libs.db.models import CorpusJob, User, UserLabel, UserModel
from libs.profiles.repository import ProfileFiles
from libs.scoring import centroid
from libs.scoring.features import FEATURE_SCHEMA_VERSION, job_features, spec_for
from libs.scoring.thresholds import DEFAULT_CUTOFF

logger = logging.getLogger(__name__)

# A model refit on one extra label is not worth the CPU (`SCHEDULER_PLAN.md` §7).
MIN_LABELS = 25
MIN_NEW_LABELS = 5

# `DEFAULT_CUTOFF` is imported from `libs.scoring.thresholds`, never restated here.


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
    """This user's labels at their current generation, joined to corpus vectors."""
    import numpy as np  # noqa: PLC0415 - the trainer image has numpy; libs importers need not

    user = session.get(User, user_id)
    if user is None:
        raise NotEnoughData("no such user")
    # Either flow counts as a profile.
    if not user.profile and ProfileFiles(session, user_id).missing_setup():
        raise NotEnoughData("user has no finished profile")

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

    # One embedder per training set, always.
    versions = {r.embedding_version for r in rows}
    if len(versions) > 1:
        raise NotEnoughData(
            f"labels span {len(versions)} embedder versions: {sorted(versions)}. "
            f"Re-embed the corpus before retraining.")

    # The keyword features need the structured profile, and a conversation-built account has none.
    spec = spec_for(user.profile)
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


# A label at or above this counts as "fit" when training.
FIT_THRESHOLD = DEFAULT_CUTOFF

# What fraction of the genuinely good jobs the operating point should catch.
TARGET_RECALL = 0.65

# Below this many labels, or too few of either class, a bake-off is theatre.
MIN_LABELS_FOR_BAKEOFF = 60
MIN_PER_CLASS = 8


def _classifiers() -> dict:
    """The candidate heads, class-balanced and emitting calibrated probabilities."""
    from sklearn.calibration import CalibratedClassifierCV  # noqa: PLC0415
    from sklearn.ensemble import HistGradientBoostingClassifier  # noqa: PLC0415
    from sklearn.linear_model import LogisticRegression  # noqa: PLC0415
    from sklearn.pipeline import make_pipeline  # noqa: PLC0415
    from sklearn.preprocessing import StandardScaler  # noqa: PLC0415
    from sklearn.svm import SVC, LinearSVC  # noqa: PLC0415

    return {
        "logreg": make_pipeline(StandardScaler(),
                                LogisticRegression(max_iter=2000, class_weight="balanced")),
        "linsvc": make_pipeline(StandardScaler(),
                                CalibratedClassifierCV(LinearSVC(class_weight="balanced"), cv=3)),
        "svc": make_pipeline(StandardScaler(),
                             CalibratedClassifierCV(SVC(class_weight="balanced"), cv=3)),
        "histgbm": HistGradientBoostingClassifier(class_weight="balanced"),
    }


def _precision_recall(actual, predicted) -> tuple[float, float, float]:
    """Precision, recall and F1 for one boolean prediction."""
    tp = int((actual & predicted).sum())
    fp = int((~actual & predicted).sum())
    fn = int((actual & ~predicted).sum())
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1


def _default_fit(data: TrainingSet) -> dict:
    """Bake off the candidate heads and deploy the winner, as the reference does."""
    import numpy as np  # noqa: PLC0415
    from sklearn.linear_model import Ridge  # noqa: PLC0415

    targets = np.asarray(data.targets, dtype=np.float64)
    labels = (targets >= FIT_THRESHOLD).astype(int)
    positives, negatives = int(labels.sum()), int(len(labels) - labels.sum())

    if (len(targets) < MIN_LABELS_FOR_BAKEOFF
            or positives < MIN_PER_CLASS or negatives < MIN_PER_CLASS):
        estimator = Ridge(alpha=1.0)
        estimator.fit(data.matrix, targets, sample_weight=data.weights)
        return {"model": estimator, "task": "regress", "model_type": "ridge",
                "decision_cutoff": int(FIT_THRESHOLD), "keep_cutoff": 45,
                "metrics": {"note": "too few labels to bake off",
                            "positives": positives, "negatives": negatives}}

    return _bake_off(data, labels, positives, negatives)


def _bake_off(data: TrainingSet, labels, positives: int, negatives: int) -> dict:
    """Select a head on CV average precision, then tune the cutoff on out-of-fold probabilities."""
    import numpy as np  # noqa: PLC0415
    from sklearn.metrics import average_precision_score, roc_auc_score  # noqa: PLC0415
    from sklearn.model_selection import cross_val_predict, train_test_split  # noqa: PLC0415

    matrix = np.asarray(data.matrix)
    train_idx, test_idx = train_test_split(
        np.arange(len(labels)), test_size=0.2, random_state=42, stratify=labels)
    y_train, y_test = labels[train_idx], labels[test_idx]

    scored: dict[str, dict] = {}
    for name, estimator in _classifiers().items():
        try:
            oof = cross_val_predict(estimator, matrix[train_idx], y_train,
                                    cv=5, method="predict_proba")[:, 1]
            fitted = estimator.fit(matrix[train_idx], y_train)
            proba = fitted.predict_proba(matrix[test_idx])[:, 1]
        except Exception as exc:                                  # noqa: BLE001
            # One head failing to converge is not the bake-off failing.
            logger.warning("trainer: %s failed to fit (%s)", name, type(exc).__name__)
            continue
        scored[name] = {"oof": oof, "proba": proba,
                        "cv_ap": float(average_precision_score(y_train, oof)),
                        "test_ap": float(average_precision_score(y_test, proba)),
                        "test_auc": float(roc_auc_score(y_test, proba))}

    if not scored:
        raise NotEnoughData("no classifier could be fitted on this label set")

    best_name = max(scored, key=lambda name: scored[name]["cv_ap"])
    best = scored[best_name]

    # The operating point, from out-of-fold probabilities only.
    actual = y_train.astype(bool)
    sweep = [(threshold, *_precision_recall(actual, best["oof"] >= threshold / 100))
             for threshold in range(20, 91)]
    holds_recall = [row for row in sweep if row[2] >= TARGET_RECALL]

    if holds_recall:
        cutoff = holds_recall[-1][0]
        recall_met = True
    else:
        # No threshold reaches the target, so pick the best available point, never `FIT_THRESHOLD`.
        cutoff = max(sweep, key=lambda row: row[3])[0]
        recall_met = False
        logger.warning("trainer: no cutoff in 20-90 reaches %.0f%% recall out of fold; using %d, "
                       "the best-F1 point", TARGET_RECALL * 100, cutoff)

    precision, recall, f1 = _precision_recall(actual, best["oof"] >= cutoff / 100)

    # Refit on everything, including the held-out split.
    final = _classifiers()[best_name].fit(matrix, labels)
    logger.info("trainer: %s selected (cv-AP %.2f, test-AP %.2f), cutoff %d at recall %.0f%%",
                best_name, best["cv_ap"], best["test_ap"], cutoff, recall * 100)

    return {
        "model": final, "task": "classify", "model_type": best_name,
        "decision_cutoff": cutoff,
        "keep_cutoff": max(cutoff - 15, 1),
        "metrics": {
            "model_type": best_name, "cv_ap": round(best["cv_ap"], 3),
            "test_ap": round(best["test_ap"], 3), "test_auc": round(best["test_auc"], 3),
            "cutoff": cutoff, "oof_precision": round(precision, 3),
            "oof_recall": round(recall, 3), "oof_f1": round(f1, 3),
            # False means the cutoff is the best-F1 compromise rather than the recall target.
            "recall_target_met": recall_met,
            "positives": positives, "negatives": negatives,
            "candidates": {name: round(row["cv_ap"], 3) for name, row in scored.items()},
        },
    }


def train_user(session: Session, user_id: _uuid.UUID, *,
               artifact_dir: Path,
               fit: Callable[[TrainingSet], dict] = _default_fit,
               cutoff: int = DEFAULT_CUTOFF) -> int:
    """Fit, publish, and return the new `model_version`."""
    user = session.get(User, user_id)
    if user is None:
        raise NotEnoughData(f"no such user: {user_id}")

    # Setup must be finished before an artifact is built from it.
    missing = ProfileFiles(session, user_id).missing_setup()
    if missing:
        raise NotEnoughData(
            f"setup is unfinished — {len(missing)} profile file(s) still missing or unfilled: "
            f"{', '.join(missing)}. Finish setup before training a model.")

    data = build_training_set(session, user_id)
    blob = dict(fit(data))
    blob.setdefault("task", "regress")
    # The cutoff comes from the fit, not from a constant.
    blob.setdefault("decision_cutoff", cutoff)
    blob.setdefault("keep_cutoff", max(int(blob["decision_cutoff"]) - 15, 1))
    blob["n_train"] = len(data.targets)

    version = (user.model_version or 0) + 1
    path = _write_artifact(artifact_dir, user_id, version, blob)

    # One active artifact per user is a partial unique index: clear the previous in the same batch.
    session.query(UserModel).filter(
        UserModel.user_id == user_id, UserModel.is_active.is_(True)).update({"is_active": False})
    # The pre-score filter, measured from the same rows the fit just used.
    built = centroid.build([
        [float(component) for component in data.matrix[index][:data.embedder_dim]]
        for index, target in enumerate(data.targets) if target >= FIT_THRESHOLD])
    if built is None:
        logger.info("trainer: %s has too few positive labels for a pre-score centroid — "
                    "its jobs are all scored", user_id)

    session.add(UserModel(
        user_id=user_id, version=version, path=str(path),
        centroid=centroid.pack(built[0]) if built else None,
        centroid_threshold=built[1] if built else None,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        profile_generation=user.profile_generation,
        embedder_model=data.embedder_model, embedder_dim=data.embedder_dim,
        embedder_version=data.embedder_version,
        n_labels=len(data.targets), is_active=True,
        # Everything the fit measured, so "did this retrain help" is answerable from the row.
        metrics={"n_train": len(data.targets), "decision_cutoff": blob["decision_cutoff"],
                 "task": blob["task"], **(blob.get("metrics") or {})}))
    user.model_version = version
    notifylib.notify(session, user_id, notifylib.MODEL_TRAINED,
                     f"Version {version}, trained on {len(data.targets)} of your decisions. "
                     "Your feed is ranked by it from the next cycle.")
    return version


def should_retrain(session: Session, user_id: _uuid.UUID, *,
                   min_new: int = MIN_NEW_LABELS) -> bool:
    """Whether enough has changed since the active artifact to be worth the CPU."""
    active = session.scalar(
        select(UserModel).where(UserModel.user_id == user_id, UserModel.is_active.is_(True)))
    labelled = session.scalar(
        select(func.count()).select_from(UserLabel).where(UserLabel.user_id == user_id)) or 0
    if active is None:
        return labelled >= MIN_LABELS

    # What the training rows are in now, read from the corpus: the vectors are the ground truth.
    current = session.scalar(
        select(CorpusJob.embedding_version)
        .join(UserLabel, UserLabel.job_id == CorpusJob.id)
        .where(UserLabel.user_id == user_id, CorpusJob.embedding.is_not(None))
        .limit(1))
    if current and current != active.embedder_version:
        logger.info("trainer: %s was fitted in %r and the corpus is now %r — retraining regardless "
                    "of label count", user_id, active.embedder_version, current)
        return True

    return labelled - (active.n_labels or 0) >= min_new


def _write_artifact(artifact_dir: Path, user_id: _uuid.UUID, version: int, blob: dict) -> Path:
    """Temp file then rename."""
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
