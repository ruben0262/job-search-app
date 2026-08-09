"""Cold start: getting a brand-new account from zero labels to its own model."""
from __future__ import annotations

import logging
import uuid as _uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from libs.db.models import CorpusJob, User, UserLabel
from libs.labels import labelling_state, target_for
from libs.llm import LlmClient, LlmError
from libs.obs import audit
from libs.tenant.repository import TenantRepository
from services.labeller.label import BATCH_SIZE, score_batch, stale_job_ids, write_labels
from services.labeller.pool import MIN_VIABLE_POOL, select_pool

logger = logging.getLogger(__name__)

# When a cold start has bought enough labels to be worth its first fit. Not the trainer's floor:
# `services.trainer.train.MIN_LABELS_TO_FIT` is the point below which a fit is refused outright.
LABELS_BEFORE_FIRST_FIT = 200


def cold_start_batch(session: Session, user_id: _uuid.UUID, client: LlmClient, *,
                     size: int = BATCH_SIZE) -> tuple[int, int]:
    """Label one banded batch of unlabelled jobs."""
    user = session.get(User, user_id)
    if user is None or not user.rubric_md:
        raise LlmError("this account has no rubric — confirm a profile first")

    config = TenantRepository(session).get_config(user_id)
    if not config:
        raise LlmError("this account has no config — there is nothing to select a pool from")

    job_ids = select_pool(session, user_id, config, size=size)
    if not job_ids:
        # Not an error, and **not "nothing owed"**. Returning 0 here for six days told the lane this
        # account had finished labelling when it was 57 short of its target: the empty pool is a
        # statement about the corpus this minute, not about how many labels are still wanted. The
        # caller must be able to tell the two apart, so the shortfall is reported either way.
        logger.warning("user %s: cold start found no unlabelled jobs matching their config",
                       user_id)
        return 0, _shortfall(session, user)

    jobs = list(session.scalars(select(CorpusJob).where(CorpusJob.id.in_(job_ids))).all())
    written = write_labels(session, user_id, score_batch(client, user.rubric_md, jobs), jobs)
    session.flush()

    labels = session.scalar(
        select(func.count()).select_from(UserLabel)
        .where(UserLabel.user_id == user_id,
               UserLabel.profile_generation == user.profile_generation)) or 0
    remaining = max(target_for(user) - labels, 0)

    audit(logger, "coldstart.batch", user=user_id, written=written, labels=labels,
          remaining=remaining)
    return written, remaining


def _shortfall(session: Session, user: User) -> int:
    """How many labels this account is still short of its target."""
    labels = session.scalar(
        select(func.count()).select_from(UserLabel)
        .where(UserLabel.user_id == user.id,
               UserLabel.profile_generation == user.profile_generation)) or 0
    return max(target_for(user) - labels, 0)


def ready_to_train(session: Session, user_id: _uuid.UUID) -> bool:
    """Whether there are enough labels at the current generation to be worth a fit."""
    user = session.get(User, user_id)
    if user is None:
        return False
    labels = session.scalar(
        select(func.count()).select_from(UserLabel)
        .where(UserLabel.user_id == user_id,
               UserLabel.profile_generation == user.profile_generation)) or 0
    # Never above what this account is allowed to buy: an operator who lowers `label_pool` below
    # the constant would otherwise leave the account permanently one label short of training.
    needed = min(LABELS_BEFORE_FIRST_FIT, target_for(user))
    if labels < needed:
        logger.info("user %s has %d labels, below the %d needed to train",
                    user_id, labels, needed)
    return labels >= needed


def next_batch(session: Session, user_id: _uuid.UUID, client: LlmClient) -> tuple[str, int, int]:
    """Do whatever the account needs next."""
    state = labelling_state(session, user_id)
    if state["action"] == "relabel":
        from services.labeller.label import relabel_user  # noqa: PLC0415 - avoids a cycle

        written, remaining = relabel_user(session, user_id, client)
        return "relabel", written, remaining
    if state["action"] == "cold_start":
        written, remaining = cold_start_batch(session, user_id, client)
        return "cold_start", written, remaining

    # Nothing owed.
    from services.labeller.shadow_lane import run_shadow  # noqa: PLC0415 - avoids a cycle

    user = session.get(User, user_id)
    if user is not None and user.model_version:
        compared, _ = run_shadow(session, user_id, client)
        if compared:
            return "shadow", compared, 0
    return "train", 0, 0


# Re-exported so the worker imports one name rather than reaching past this module into two others.
__all__ = ["LABELS_BEFORE_FIRST_FIT", "MIN_VIABLE_POOL", "cold_start_batch",
           "labelling_state", "next_batch", "ready_to_train", "stale_job_ids"]
