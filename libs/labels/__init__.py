"""What an account needs next from the label lane."""
from __future__ import annotations

import uuid as _uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from libs.db.models import User, UserLabel


def pool_target() -> int:
    """The catalogue's label pool, for a caller with no account to resolve against."""
    from libs.limits import limit_default  # noqa: PLC0415 - avoids a cycle at import time

    return int(limit_default("label_pool"))


def target_for(user: User) -> int:
    """How many labels this account is aiming for."""
    from libs.limits import resolve  # noqa: PLC0415 - avoids a cycle at import time

    ceiling = resolve(user, "label_pool")
    return pool_target() if ceiling is None else max(int(ceiling), 1)


def labelling_state(session: Session, user_id: _uuid.UUID) -> dict:
    """What this account needs next from the `label` lane, and how far along it is."""
    user = session.get(User, user_id)
    if user is None or not user.rubric_md:
        return {"action": "confirm_profile", "labels": 0, "target": pool_target(), "stale": 0}

    labels = session.scalar(
        select(func.count()).select_from(UserLabel)
        .where(UserLabel.user_id == user_id,
               UserLabel.profile_generation == user.profile_generation)) or 0
    stale = session.scalar(
        select(func.count()).select_from(UserLabel)
        .where(UserLabel.user_id == user_id,
               UserLabel.profile_generation < user.profile_generation)) or 0

    target = target_for(user)
    if stale:
        # Relabelling first, always.
        action = "relabel"
    elif labels < target:
        action = "cold_start"
    else:
        action = "train"
    return {"action": action, "labels": labels, "target": target, "stale": stale}

__all__ = ["labelling_state", "pool_target", "target_for"]
