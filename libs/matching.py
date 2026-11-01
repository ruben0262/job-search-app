"""Which jobs a person sees: their green words pick them, their red words veto them. Nothing learns.

The one definition behind the jobs page and the alerts (CLAUDE.md §1): both ask `matching_jobs`. Red
words, language, place and the freshness window are `libs.tenant.filters`, unchanged; this module adds
the green words, the minimum salary and the order.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime

from pymongo.database import Database

from libs.contracts.config import migrate_config
from libs.corpus import salary
from libs.db.models import CorpusJob, User
from libs.tenant.filters import candidate_conditions, filters_for
from libs.tenant.repository import TenantRepository

logger = logging.getLogger(__name__)

# A green word in the title says far more than one in the text.
TITLE_POINTS, TEXT_POINTS = 75, 25
# Requirements sit at the top of a posting and boilerplate below it, so only the top is read.
TEXT_CHARS = 2_500
# How many of the newest candidates one call ranks.
SCAN = 2_000
# What `UserFilters.unwanted` reads off a row.
_ROW = ("title", "company", "description", "source", "applicants", "location", "is_remote",
        "workplace", "language")
# PCRE's `\w` is ASCII unless told otherwise; this makes a word boundary mean what Python's does.
_UNICODE = "(*UCP)"


@dataclass(frozen=True)
class Match:
    job: CorpusJob
    score: int
    # The green words found, so the page can say why a job is there.
    words: tuple[str, ...]


def norm(text: str) -> str:
    """A word as green and red words are compared: lower case, spaces and hyphens alike."""
    return re.sub(r"[\s-]+", " ", text.strip().lower())


def green_words(config: dict | None) -> list[str]:
    """Every green word across the config's sections, once each."""
    sections = (migrate_config(config) if config else {}).get("sections") or []
    return sorted({norm(word) for section in sections
                   for word in section.get("keywords") or [] if word.strip()})


def pattern(words: list[str]) -> re.Pattern | None:
    """One regex for every word: whole words only, a space and a hyphen treated alike."""
    parts = [r"[\s-]+".join(map(re.escape, word.split())) for word in words]
    return re.compile(rf"(?<!\w)(?:{'|'.join(parts)})(?!\w)", re.I) if parts else None


def score(regex: re.Pattern, title: str | None,
          description: str | None) -> tuple[int, tuple[str, ...]]:
    """Points for where the green words appear, and which ones; `(0, ())` for none."""
    in_title = {norm(found) for found in regex.findall(title or "")}
    in_text = {norm(found) for found in regex.findall((description or "")[:TEXT_CHARS])}
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


def matching_jobs(db: Database, user: User, *, since: datetime | None = None,
                  scan: int = SCAN) -> list[Match]:
    """This person's matches, best first, then newest first."""
    config = TenantRepository(db).get_config(user.id)
    regex = pattern(green_words(config))
    if regex is None:
        return []

    filters = filters_for(db, user)
    where = candidate_conditions(filters)
    if since is not None:
        where.append({"first_seen_at": {"$gt": since}})
    # The newest `scan` candidates, then their green words judged in the database: only matches
    # cross the network, where pulling every candidate's text would be megabytes per page.
    in_database = _UNICODE + regex.pattern
    head = {"$substrCP": [{"$ifNull": ["$description", ""]}, 0, TEXT_CHARS]}
    jobs = [CorpusJob.from_doc(row) for row in db[CorpusJob.COLLECTION].aggregate([
        {"$match": {"$and": where}},
        {"$sort": {"first_seen_at": -1, "_id": -1}},
        {"$limit": scan},
        {"$match": {"$or": [
            {"title": {"$regex": in_database, "$options": "i"}},
            {"$expr": {"$regexMatch": {"input": head, "regex": in_database, "options": "i"}}}]}},
    ])]
    if since is not None and db[CorpusJob.COLLECTION].count_documents({"$and": where}, limit=scan) == scan:
        # The caller's watermark will pass every older candidate this scan never reached.
        logger.warning("matching: %s has at least %d new candidates since %s; only the newest %d were "
                       "examined, so older matches will not be alerted (the jobs page still lists them)",
                       user.id, scan, since.isoformat(), scan)

    found = []
    for job in jobs:
        # Scored here to say which words, and where: the database only said that some matched.
        points, words = score(regex, job.title, job.description)
        if (points and not filters.unwanted({name: getattr(job, name) for name in _ROW})
                and not underpaid(config, job.description)):
            found.append(Match(job, points, words))
    # A stable sort, so equal scores keep the query's newest-first order.
    return sorted(found, key=lambda match: match.score, reverse=True)
