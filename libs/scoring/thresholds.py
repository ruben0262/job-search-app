"""The score at which a job counts as a match. One number, shared by everything that shows one.

**Three places had to agree and only two did.** The notifier delivers at 60; the card called
anything under 80 borderline until it was fixed; the jobs table showed every scored row at all.
Each was defensible alone and together they told the user three different stories about the same
job — sent to Telegram, marked "do not get excited", and buried in a page of noise.

It lives in `libs` because `services` may not import `services`, and the alternative was three
constants that start equal and drift. That is not a hypothetical: two of the three had already
drifted before this file existed.

Matches the reference's `min_score_for_cv` — the score at which it will build a CV without being
asked. So the meaning is "worth acting on", not "exceptional".
"""
from __future__ import annotations

DEFAULT_CUTOFF = 60.0

# Below this a job is not worth keeping in a per-user feed at all. The reference's
# `min_score_to_keep`; retained here because the scorer's `_decide` needs a floor for
# "needs-review" and the number should not be invented separately in two services.
DEFAULT_KEEP = 45.0


def cutoff_for(session, user_id) -> float:
    """The score at which this user's jobs are worth sending.

    **Read from their active model, not from the constant.** The cutoff is an operating point the
    training run tuned on out-of-fold predictions — the reference instance runs at 44 — while
    `DEFAULT_CUTOFF` is the label threshold that decides what counts as "fit" while *training*.
    Using one for the other is what emptied the feed: a model asked for 60 when its own tuned
    answer was 44 delivers almost nothing, and nothing says why.

    Falls back to the constant for an account with no model yet, where the keyword ranker is
    producing the scores and there is no tuned answer to read.
    """
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
