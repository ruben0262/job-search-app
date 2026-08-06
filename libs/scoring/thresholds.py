"""The score at which a job counts as a match."""
from __future__ import annotations

DEFAULT_CUTOFF = 60.0

def cutoff_for(session, user_id) -> float:
    """The score at which this user's jobs are worth sending."""
    from sqlalchemy import select  # noqa: PLC0415

    from libs.db.models import User, UserModel  # noqa: PLC0415 - avoids an import cycle

    user = session.get(User, user_id)
    if user is None:
        return DEFAULT_CUTOFF
    metrics = session.scalar(
        select(UserModel.metrics)
        .where(UserModel.user_id == user_id, UserModel.is_active.is_(True))
        .limit(1))
    tuned = (metrics or {}).get("decision_cutoff")
    return float(tuned) if tuned else DEFAULT_CUTOFF
