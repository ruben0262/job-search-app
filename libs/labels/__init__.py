"""What an account needs next from the label lane.

Lives here rather than in `services/labeller` because two callers need the same answer: the
worker that acts on it, and the page that shows a user what training is doing. A service may
not import another service, and copying the rule into the API is exactly how three separate
"two definitions of ready" bugs happened in one day.
"""
from __future__ import annotations

import uuid as _uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from libs.db.models import User, UserLabel

# How many labels a cold start aims for before the first fit, when an operator has not said
# otherwise. **The per-account ceiling wins**: labelling runs on the user's own credential, so how
# much of it to buy is a decision about their money, and `label_pool` is where an operator makes it.
POOL_SIZE = 300


def target_for(user: User) -> int:
    """How many labels this account is aiming for.

    The catalogue entry existed and nothing read it, so the admin control read as a setting and
    behaved as decoration. Unlimited resolves to the default rather than to no target at all: a
    cold start with no finish line would label the entire corpus on somebody else's card.
    """
    from libs.limits import resolve  # noqa: PLC0415 - avoids a cycle at import time

    ceiling = resolve(user, "label_pool")
    return POOL_SIZE if ceiling is None else max(int(ceiling), 1)


def labelling_state(session: Session, user_id: _uuid.UUID) -> dict:
    """What this account needs next from the `label` lane, and how far along it is.

    Derived, like every other state on the platform, so it cannot disagree with reality.
    """
    user = session.get(User, user_id)
    if user is None or not user.rubric_md:
        return {"action": "confirm_profile", "labels": 0, "target": POOL_SIZE, "stale": 0}

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
        # Relabelling first, always. These are jobs the user already cared about, and carrying them
        # forward is cheaper per unit of signal than buying opinions on new ones.
        action = "relabel"
    elif labels < target:
        action = "cold_start"
    else:
        action = "train"
    return {"action": action, "labels": labels, "target": target, "stale": stale}

__all__ = ["POOL_SIZE", "labelling_state", "target_for"]
