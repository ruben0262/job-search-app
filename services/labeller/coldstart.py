"""Cold start: getting a brand-new account from zero labels to its own model.

The stages a user actually experiences (`COLD_START_PLAN.md` §2):

| Stage | When | What ranks their jobs |
|---|---|---|
| 0 | the moment they save a config | the config itself, via `libs.scoring.rules` — free, instant, crude |
| 2 | after this module has run and the trainer has fit | their own classifier |

Stage 1 — a bootstrap model trained on other users' labels — is **not built**, deliberately. It is a
data-use question requiring consent, and the version trained only on the operator's labels is a
separate artifact to build and version for a gap that stage 0 already covers. Recorded as a
decision rather than an omission.

**One entry point, state-driven.** The `label` lane has a single job kind and this decides what the
account needs: carry existing labels across a rubric change, or buy a first set. The alternative —
two job kinds enqueued by whoever knows which is right — needs every caller to re-derive the rule,
and the first one to get it wrong spends a user's money labelling jobs they already had.
"""
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
from services.labeller.pool import MIN_VIABLE_POOL, POOL_SIZE, select_pool

logger = logging.getLogger(__name__)

# Enough labels to fit something worth serving. From `COLD_START_PLAN.md` §7: if backfill labelling
# partially fails, train on what succeeded provided there are at least this many, rather than
# throwing away a nearly-complete pool because the last batch errored.
MIN_LABELS_TO_TRAIN = 100


def cold_start_batch(session: Session, user_id: _uuid.UUID, client: LlmClient, *,
                     size: int = BATCH_SIZE) -> tuple[int, int]:
    """Label one banded batch of unlabelled jobs. Returns (written, remaining to reach the target).

    The pool is re-selected each batch rather than chosen once and stored. That costs a ranking pass
    over recent corpus rows and buys two things: a batch is balanced across the high/mid/low bands
    *every time*, so an interrupted cold start still leaves a usable training set rather than 200
    likely-positives; and there is no pool table to keep in step with a corpus that changes under it.
    """
    user = session.get(User, user_id)
    if user is None or not user.rubric_md:
        raise LlmError("this account has no rubric — confirm a profile first")

    config = TenantRepository(session).get_config(user_id)
    if not config:
        raise LlmError("this account has no config — there is nothing to select a pool from")

    job_ids = select_pool(session, user_id, config, size=size)
    if not job_ids:
        # Not an error. The config matches nothing in the corpus, which is a real answer and one
        # the user can act on — it is the `<150 jobs` failure mode of §7, and the config form's
        # live match count is where it should have been caught.
        logger.warning("user %s: cold start found no unlabelled jobs matching their config",
                       user_id)
        return 0, 0

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


def ready_to_train(session: Session, user_id: _uuid.UUID) -> bool:
    """Whether there are enough labels at the current generation to be worth a fit.

    `MIN_LABELS_TO_TRAIN` rather than `POOL_SIZE`, because a cold start that lost its last batch to
    a provider error should still produce a model. A user with 280 labels and no model because 20
    calls failed is the worst outcome available.
    """
    user = session.get(User, user_id)
    if user is None:
        return False
    labels = session.scalar(
        select(func.count()).select_from(UserLabel)
        .where(UserLabel.user_id == user_id,
               UserLabel.profile_generation == user.profile_generation)) or 0
    if labels < MIN_LABELS_TO_TRAIN:
        logger.info("user %s has %d labels, below the %d needed to train",
                    user_id, labels, MIN_LABELS_TO_TRAIN)
    return labels >= MIN_LABELS_TO_TRAIN


def next_batch(session: Session, user_id: _uuid.UUID, client: LlmClient) -> tuple[str, int, int]:
    """Do whatever the account needs next. Returns (what was done, written, remaining).

    This is the whole `label` lane in one function, and the reason it is one function is that the
    ordering — stale labels before new ones — is a rule that must hold everywhere or nowhere.
    """
    state = labelling_state(session, user_id)
    if state["action"] == "relabel":
        from services.labeller.label import relabel_user  # noqa: PLC0415 - avoids a cycle

        written, remaining = relabel_user(session, user_id, client)
        return "relabel", written, remaining
    if state["action"] == "cold_start":
        written, remaining = cold_start_batch(session, user_id, client)
        return "cold_start", written, remaining

    # Nothing owed. **This is where the model gets checked rather than trusted.** A served model
    # drifts silently — no error, no alert, just numbers that stop meaning what they meant — so an
    # account with nothing else to do spends a small sample asking the judge whether it still
    # agrees. Last on purpose: a backlog of stale labels is work the user is waiting on, and a
    # health check never competes with it.
    from services.labeller.shadow_lane import run_shadow  # noqa: PLC0415 - avoids a cycle

    user = session.get(User, user_id)
    if user is not None and user.model_version:
        compared, _ = run_shadow(session, user_id, client)
        if compared:
            return "shadow", compared, 0
    return "train", 0, 0


# Re-exported so the worker imports one name rather than reaching past this module into two others.
__all__ = ["MIN_LABELS_TO_TRAIN", "MIN_VIABLE_POOL", "POOL_SIZE", "cold_start_batch",
           "labelling_state", "next_batch", "ready_to_train", "stale_job_ids"]
