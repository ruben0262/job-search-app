"""Every model an account has ever had, with what the fit measured.

**The training run already knows all of this and nobody could see it.** The Model page said "version
N, live" and stopped there, while `models.metrics` held the head that won, its cross-validated
average precision, the held-out estimate, the tuned cutoff and the out-of-fold precision and recall
— for every version, because a superseded model is deactivated rather than deleted.

That gap has a cost. On 2026-07-31 this account went Ridge at an untuned cutoff of 60 → `svc` at 38
→ `histgbm` at 42, and precision at the same recall rose from 0.616 to 0.688. It was a real
improvement and the only way to see it was to run SQL by hand. A user paying for their own training
should be able to answer "did that help" themselves, and an operator asked "why is this person's
feed bad" should not have to open a database client.

One builder, two callers: the user reads their own on the Model page, an operator reads anybody's on
the admin account page. Same numbers, so the two views cannot disagree about what happened.
"""
from __future__ import annotations

import uuid as _uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from libs.db.models import UserModel

# What the fit measured, in the order a reader wants it: which head won, how well it ranked, where
# the operating point landed, and what that point costs in precision and recall.
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
            # **Absent rather than zero when a run did not measure something.** The early Ridge
            # models recorded only `n_train`, and rendering those as 0.0 would read as a model that
            # scored nothing rather than one nobody measured.
            "metrics": [{"key": key, "label": label, "value": metrics[key]}
                        for key, label in _REPORTED if metrics.get(key) is not None],
            # The losing candidates and their scores, so "why this head" is answerable.
            "candidates": metrics.get("candidates") or {},
            "recall_target_met": metrics.get("recall_target_met"),
            "positives": metrics.get("positives"),
            "negatives": metrics.get("negatives"),
        })
    return history
