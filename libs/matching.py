"""Which jobs a person sees: their green words pick them, their red words veto them. Nothing learns.

The one definition behind the jobs page and the alerts (CLAUDE.md §1): both ask `matching_jobs`. Red
words, language, place and the freshness window are `libs.tenant.filters`, unchanged; this module adds
the green words and the order.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from libs.contracts.config import migrate_config
from libs.db.models import CorpusJob, User, UserConfig
from libs.tenant.filters import candidate_conditions, filters_for

# A green word in the title says far more than one in the text.
TITLE_POINTS, TEXT_POINTS = 75, 25
# Requirements sit at the top of a posting and boilerplate below it, so only the top is read.
TEXT_CHARS = 2_500
# How many of the newest candidates one call ranks.
SCAN = 2_000
# What `UserFilters.unwanted` reads off a row.
_ROW = ("title", "company", "description", "source", "applicants", "location", "is_remote",
        "workplace", "language")


@dataclass(frozen=True)
class Match:
    job: CorpusJob
    score: int
    # The green words found, so the page can say why a job is there.
    words: tuple[str, ...]


def _norm(text: str) -> str:
    return re.sub(r"[\s-]+", " ", text.strip().lower())


def green_words(config: dict | None) -> list[str]:
    """Every green word across the config's sections, once each."""
    sections = (migrate_config(config) if config else {}).get("sections") or []
    return sorted({_norm(word) for section in sections
                   for word in section.get("keywords") or [] if word.strip()})


def pattern(words: list[str]) -> re.Pattern | None:
    """One regex for every word: whole words only, a space and a hyphen treated alike."""
    parts = [r"[\s-]+".join(map(re.escape, word.split())) for word in words]
    return re.compile(rf"(?<!\w)(?:{'|'.join(parts)})(?!\w)", re.I) if parts else None


def score(regex: re.Pattern, title: str | None,
          description: str | None) -> tuple[int, tuple[str, ...]]:
    """Points for where the green words appear, and which ones; `(0, ())` for none."""
    in_title = {_norm(found) for found in regex.findall(title or "")}
    in_text = {_norm(found) for found in regex.findall((description or "")[:TEXT_CHARS])}
    points = TITLE_POINTS * bool(in_title) + TEXT_POINTS * bool(in_text)
    return points, tuple(sorted(in_title | in_text))


def matching_jobs(session: Session, user: User, *, since: datetime | None = None,
                  scan: int = SCAN) -> list[Match]:
    """This person's matches, best first, then newest first."""
    config = session.scalar(select(UserConfig.config).where(UserConfig.user_id == user.id))
    regex = pattern(green_words(config))
    if regex is None:
        return []

    filters = filters_for(session, user)
    where = candidate_conditions(filters)
    if since is not None:
        where.append(CorpusJob.first_seen_at > since)
    jobs = session.scalars(select(CorpusJob).where(*where)
                           .order_by(CorpusJob.first_seen_at.desc()).limit(scan)).all()

    found = []
    for job in jobs:
        if filters.unwanted({name: getattr(job, name) for name in _ROW}):
            continue
        points, words = score(regex, job.title, job.description)
        if points:
            found.append(Match(job, points, words))
    # A stable sort, so equal scores keep the query's newest-first order.
    return sorted(found, key=lambda match: match.score, reverse=True)
