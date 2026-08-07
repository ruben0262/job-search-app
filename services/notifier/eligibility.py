"""Which of a user's scored jobs are worth sending, and in what order."""
from __future__ import annotations

import logging

from libs.corpus.ranking import priority_key

logger = logging.getLogger(__name__)

# How many scored rows to consider per digest slot.
CANDIDATE_MULTIPLIER = 5


def is_eligible(item) -> tuple[bool, str | None]:
    """Whether to send this job, and the reason if not."""
    return (False, "closed") if getattr(item, "closed", False) else (True, None)


def rank(items: list, *, first_seen: dict[int, object] | None = None, now=None) -> list:
    """Sort by opportunity, best first."""
    first_seen = first_seen or {}
    return sorted(items, key=lambda item: priority_key(
        posted_at=item.posted_at, first_seen_at=first_seen.get(item.job_id),
        applicants=item.applicants, reposted=item.reposted,
        workplace=item.workplace, score=int(item.score or 0), now=now))


def select(items: list, *, limit: int,
           first_seen: dict[int, object] | None = None,
           now=None) -> tuple[list, dict[str, int]]:
    """Rank, drop anything that closed since it was scored, and take the top `limit`."""
    kept, dropped = [], {}
    for item in items:
        eligible, reason = is_eligible(item)
        if eligible:
            kept.append(item)
        else:
            dropped[reason] = dropped.get(reason, 0) + 1
    return rank(kept, first_seen=first_seen, now=now)[:limit], dropped
