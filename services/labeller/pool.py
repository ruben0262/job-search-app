"""Choosing which jobs a new account pays to have labelled."""
from __future__ import annotations

import logging
import uuid as _uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from libs.contracts.config import migrate_config
from libs.db.models import CorpusJob, UserLabel
from libs.labels import pool_target
from libs.scoring.rules import POOL_BANDS

logger = logging.getLogger(__name__)


# Below this the config is too narrow to train on, and saying so beats a useless model.
MIN_VIABLE_POOL = 150

# How far back to draw from.
LOOKBACK_DAYS = 90

# How many candidates to rank before banding.
MAX_CANDIDATES = 5_000

# How many of the user's keywords narrow the candidate query.
MAX_TERMS_IN_QUERY = 25



def _config_terms(config: dict) -> list[str]:
    """The user's own search words, for narrowing the candidate query."""
    terms: list[str] = []
    for section in (config or {}).get("sections") or []:
        for keyword in section.get("keywords") or []:
            cleaned = " ".join(str(keyword).split()).strip().lower()
            if len(cleaned) >= 3 and cleaned not in terms:
                terms.append(cleaned)
    return terms[:MAX_TERMS_IN_QUERY]


def select_pool(session: Session, user_id: _uuid.UUID, config: dict, *,
                size: int | None = None) -> list[int]:
    """Corpus ids to label for this account, banded high/mid/low."""
    size = pool_target() if size is None else size
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415 - one caller, one path

    from libs.scoring.rules import score_job  # noqa: PLC0415 - avoids a cycle at import time

    already = set(session.scalars(
        select(UserLabel.job_id).where(UserLabel.user_id == user_id)).all())

    since = datetime.now(UTC) - timedelta(days=LOOKBACK_DAYS)

    # Drawn per user, not "the newest rows on the platform".
    terms = _config_terms(config)
    rows: list[CorpusJob] = []
    seen_ids: set[int] = set()

    if terms:
        matched = session.scalars(
            select(CorpusJob)
            .where(CorpusJob.first_seen_at >= since,
                   or_(*[CorpusJob.title.ilike(f"%{term}%") for term in terms]))
            .order_by(CorpusJob.first_seen_at.desc())
            .limit(MAX_CANDIDATES)).all()
        rows.extend(matched)
        seen_ids.update(row.id for row in matched)

    # The remainder of the budget, newest first, so a config with no usable terms still works.
    spare = max(MAX_CANDIDATES - len(rows), MAX_CANDIDATES // 5)
    for row in session.scalars(
            select(CorpusJob)
            .where(CorpusJob.first_seen_at >= since)
            .order_by(CorpusJob.first_seen_at.desc())
            .limit(spare)).all():
        if row.id not in seen_ids:
            rows.append(row)

    config = migrate_config(config)
    scored: list[tuple[int, int, str]] = []
    for row in rows:
        if row.id in already:
            continue
        result = score_job(config, title=row.title, description=row.description,
                           is_remote=row.is_remote)
        if not result.excluded:
            scored.append((result.score, row.id, row.source or ""))

    if not scored:
        logger.warning("user %s: no corpus job matches their config at all", user_id)
        return []

    scored.sort(key=lambda item: item[0], reverse=True)
    return _band(scored, size)


def _band(scored: list[tuple[int, int, str]], size: int) -> list[int]:
    """Split the ranked list into three bands and draw the configured share from each."""
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

    # Top up from what is left if a band ran short, rather than silently returning 180 of 300.
    if len(picked) < size:
        chosen = set(picked)
        picked.extend(job_id for _, job_id, _ in scored
                      if job_id not in chosen)  # already ranked, so this stays ordered
    return picked[:size]
