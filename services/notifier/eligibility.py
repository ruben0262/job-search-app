"""Which of a user's scored jobs go out first."""
from __future__ import annotations

import logging

from libs.corpus.ranking import priority_key

logger = logging.getLogger(__name__)

# How many scored rows to consider per digest slot.
CANDIDATE_MULTIPLIER = 5


def rank(items: list, *, first_seen: dict[int, object] | None = None, now=None) -> list:
    """Sort by opportunity, best first."""
    first_seen = first_seen or {}
    return sorted(items, key=lambda item: priority_key(
        posted_at=item.posted_at, first_seen_at=first_seen.get(item.job_id),
        applicants=item.applicants, reposted=item.reposted,
        workplace=item.workplace, score=int(item.score or 0), now=now))
