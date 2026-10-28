"""Which jobs a person sees: their green words pick them, their red words veto them. Nothing learns.

The one definition behind the jobs page and the alerts (CLAUDE.md §1): both ask `matching_jobs`. Red
words, language, place and the freshness window are `libs.tenant.filters`, unchanged; this module adds
the green words, the minimum salary and the order.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, defer

from libs.contracts.config import migrate_config
from libs.corpus import salary
from libs.db.models import CorpusJob, User, UserConfig
from libs.tenant.filters import candidate_conditions, filters_for
from libs.tenant.repository import TenantRepository

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


def underpaid(config: dict | None, description: str | None) -> bool:
    """The posting states a top salary below this person's minimum. Unknown is never underpaid."""
    minimum = (config or {}).get("min_salary")
    stated = salary.extract(description) if minimum else None
    # No period is no unit: "€5,000" could be a month or a year, so it says nothing either way.
    if not stated or not stated["period"]:
        return False
    # Private to a module kept byte-identical to the reference, and the one converter there is.
    to_eur = salary._to_eur_annual
    top = to_eur(stated["max"], stated["currency"], stated["period"])
    floor = to_eur(minimum, (config or {}).get("salary_currency") or "EUR", "year")
    return top is not None and floor is not None and top < floor


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
    # Decision D3: LinkedIn rows are fetched through someone's own proxy, so only a person with a
    # healthy one of their own sees them. Without this, everyone saw rows others' proxies paid for.
    if not TenantRepository(session).linkedin_visible(user.id):
        where.append(CorpusJob.source != "linkedin")
    # The vector is the switched-off model's, ~3 KB a row, and nothing here reads it.
    jobs = session.scalars(select(CorpusJob).options(defer(CorpusJob.embedding)).where(*where)
                           .order_by(CorpusJob.first_seen_at.desc()).limit(scan)).all()

    found = []
    for job in jobs:
        # The cheap question first: most candidates carry none of their words, and the filters'
        # workplace classifier is the costliest step in the loop.
        points, words = score(regex, job.title, job.description)
        if (points and not filters.unwanted({name: getattr(job, name) for name in _ROW})
                and not underpaid(config, job.description)):
            found.append(Match(job, points, words))
    # A stable sort, so equal scores keep the query's newest-first order.
    return sorted(found, key=lambda match: match.score, reverse=True)
