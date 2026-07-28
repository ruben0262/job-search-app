"""Choosing which jobs a new account pays to have labelled.

**Labelling a random sample is mostly wasted money.** Most of the corpus is an obvious reject for
any given person, and an obvious reject teaches a classifier almost nothing — it already knows.
`COLD_START_PLAN.md` §4 selects for *balance and boundary* instead: 40% likely positives so the
model learns what good looks like, 40% from the middle where the decision boundary actually is, and
20% clear negatives as a calibration floor.

Ranked by the rules-only score (`libs.scoring.rules`) rather than by a bootstrap model. That is a
deliberate simplification for launch: a bootstrap model trained on other users' labels is a
data-use question needing consent, and one trained only on the operator's labels is a separate
artifact to build and version. The rules score is free, needs no consent, and is good enough to
*stratify* — which is all it is asked to do here. It picks which jobs to ask about; Haiku still
answers.

The spread across sources and recency is not decoration. A pool drawn only from the largest source
teaches the model that source's phrasing, and one drawn only from this week teaches whatever this
week's market happens to be hiring for.
"""
from __future__ import annotations

import logging
import uuid as _uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from libs.contracts.config import migrate_config
from libs.db.models import CorpusJob, UserLabel
from libs.scoring.rules import POOL_BANDS

logger = logging.getLogger(__name__)

# The target pool. ~$0.54 of Haiku at the measured $0.0018/job, or ~$0.27 through the Batch API.
# Cold start is a latency and UX problem, not a cost problem (`COLD_START_PLAN.md` §4).
POOL_SIZE = 300

# Below this, the config is too narrow to train on and the honest answer is to say so rather than
# to train a model on 40 examples and let the user discover it is useless.
MIN_VIABLE_POOL = 150

# How far back to draw from. Wider than the 24-hour steady-state window on purpose: a new account
# has no history, and the last day alone would show it almost nothing.
LOOKBACK_DAYS = 90

# How many candidates to rank before banding. Bounded because this runs in a worker on a corpus
# that will keep growing, and the bands do not get better for having sorted ten times as many.
MAX_CANDIDATES = 5_000


def select_pool(session: Session, user_id: _uuid.UUID, config: dict, *,
                size: int = POOL_SIZE) -> list[int]:
    """Corpus ids to label for this account, banded high/mid/low. Excludes anything already labelled.

    Returns fewer than `size` when the corpus cannot supply it — the caller decides whether that is
    too few to train on, because "too few" is a product judgement and this is a selection function.
    """
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415 - one caller, one path

    from libs.scoring.rules import score_job  # noqa: PLC0415 - avoids a cycle at import time

    already = set(session.scalars(
        select(UserLabel.job_id).where(UserLabel.user_id == user_id)).all())

    since = datetime.now(UTC) - timedelta(days=LOOKBACK_DAYS)
    rows = session.scalars(
        select(CorpusJob)
        .where(CorpusJob.first_seen_at >= since)
        .order_by(CorpusJob.first_seen_at.desc())
        .limit(MAX_CANDIDATES)).all()

    config = migrate_config(config)
    scored: list[tuple[int, int, str]] = []
    for row in rows:
        if row.id in already:
            continue
        result = score_job(config, title=row.title, description=row.description,
                           location=row.location, is_remote=row.is_remote)
        if not result.excluded:
            scored.append((result.score, row.id, row.source or ""))

    if not scored:
        logger.warning("user %s: no corpus job matches their config at all", user_id)
        return []

    scored.sort(key=lambda item: item[0], reverse=True)
    return _band(scored, size)


def _band(scored: list[tuple[int, int, str]], size: int) -> list[int]:
    """Split the ranked list into three bands and draw the configured share from each.

    Drawn by **striding** through each band rather than taking its head. Taking the head of the
    high band gives 120 near-identical top-scoring jobs — the same phrasing, often the same
    employer — which is 120 labels' worth of money for one label's worth of information.
    """
    total = len(scored)
    third = max(total // 3, 1)
    bands = {"high": scored[:third], "mid": scored[third:third * 2], "low": scored[third * 2:]}

    picked: list[int] = []
    for share, name in POOL_BANDS:
        band = bands[name]
        want = min(int(size * share), len(band))
        if want:
            stride = max(len(band) // want, 1)
            picked.extend(job_id for _, job_id, _ in band[::stride][:want])

    # Top up from whatever is left if a band ran short, so a lopsided corpus still yields a full
    # pool rather than silently returning 180 of 300.
    if len(picked) < size:
        chosen = set(picked)
        picked.extend(job_id for _, job_id, _ in scored
                      if job_id not in chosen)  # already ranked, so this stays ordered
    return picked[:size]
