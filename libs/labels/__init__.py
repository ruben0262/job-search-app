"""What an account needs next from the label lane."""
from __future__ import annotations

import logging
import uuid as _uuid

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from libs.db.models import User, UserLabel

logger = logging.getLogger(__name__)


def pool_target() -> int:
    """The catalogue's label pool, for a caller with no account to resolve against."""
    from libs.limits import limit_default  # noqa: PLC0415 - avoids a cycle at import time

    return int(limit_default("label_pool"))


def target_for(user: User) -> int:
    """How many labels this account is aiming for."""
    from libs.limits import resolve  # noqa: PLC0415 - avoids a cycle at import time

    ceiling = resolve(user, "label_pool")
    return pool_target() if ceiling is None else max(int(ceiling), 1)


def bump_generation(session: Session, user: User) -> int:
    """Invalidate the model's guesses about this account, and carry the user's own answers across.

    **A rubric change invalidates an estimate. It does not invalidate a fact.** An `llm` label is
    what the model thought a job was worth under the old wording, so it has to be bought again. A
    `feedback` label is what the person did — applied, or answered `/rate` — and that is still true
    under any wording. Left behind at the old generation it would be relabelled: `stale_job_ids`
    filters on generation and not on source, so the account would pay its own Claude credential to
    replace ground truth with a guess, and the trainer would never see the original again because
    it reads only the current generation.

    Measured on the live platform 2026-08-14, on the one account that has been bumped: of 101 jobs
    it had applied to, **one already carried an `llm` label at weight 1.0** instead of the applied
    label — the first instance of exactly this, and the whole 98-application backfill sits one
    rubric edit away from the same fate.

    `profile_version` is deliberately left alone: it is the rubric hash the row was written under,
    and for a feedback label that is a record of when the person answered, not a claim about which
    wording produced it. Returns how many answers were carried.
    """
    user.profile_generation += 1
    carried = session.execute(
        update(UserLabel)
        .where(UserLabel.user_id == user.id,
               UserLabel.source == "feedback",
               UserLabel.profile_generation < user.profile_generation)
        .values(profile_generation=user.profile_generation)).rowcount or 0
    if carried:
        logger.info("generation %d for user %s: carried %d user answer(s) forward",
                    user.profile_generation, user.id, carried)
    return carried


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

__all__ = ["bump_generation", "labelling_state", "pool_target", "target_for"]
