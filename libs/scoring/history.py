"""Every model an account has ever had, with what the fit measured."""
from __future__ import annotations

import uuid as _uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from libs.db.models import UserModel

# What the fit measured: which head won, how it ranked, and what the operating point costs.
_REPORTED = (
    ("model_type", "Head"),
    ("cv_ap", "CV avg precision"),
    ("test_ap", "Held-out avg precision"),
    ("test_auc", "Held-out AUC"),
    ("decision_cutoff", "Cutoff"),
    ("oof_precision", "Precision at cutoff"),
    ("oof_recall", "Recall at cutoff"),
)


def model_history(session: Session, user_id: _uuid.UUID, *, limit: int = 20) -> list[dict]:
    """Newest first. Every version, whether or not it is the live one."""
    rows = session.scalars(
        select(UserModel)
        .where(UserModel.user_id == user_id)
        .order_by(UserModel.version.desc())
        .limit(limit)).all()

    history = []
    for row in rows:
        metrics = row.metrics or {}
        history.append({
            "version": row.version,
            "active": bool(row.is_active),
            "n_labels": row.n_labels,
            "trained_at": row.created_at.isoformat() if row.created_at else None,
            "embedder_version": row.embedder_version,
            "profile_generation": row.profile_generation,
            # Absent rather than zero when a run did not measure something.
            "metrics": [{"key": key, "label": label, "value": metrics[key]}
                        for key, label in _REPORTED if metrics.get(key) is not None],
            # The losing candidates and their scores, so "why this head" is answerable.
            "candidates": metrics.get("candidates") or {},
            "recall_target_met": metrics.get("recall_target_met"),
            "positives": metrics.get("positives"),
            "negatives": metrics.get("negatives"),
        })
    return history
