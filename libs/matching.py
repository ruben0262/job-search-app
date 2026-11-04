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
from libs.db.models import CorpusJob, User, UserJobState, Word
from libs.tenant.filters import candidate_conditions, filters_for
from libs.tenant.repository import TenantRepository

logger = logging.getLogger(__name__)

# How many of the newest candidates one call examines.
SCAN = 2_000
# What `UserFilters.unwanted` reads off a row. Not the description: the working arrangement it would
# read there was read once already, when the text was stored (`CorpusRepository.set_description`).
_ROW = ("title", "company", "source", "applicants", "location", "is_remote", "workplace", "language")
# PCRE's `\w` is ASCII unless told otherwise; this makes a word boundary mean what Python's does.
_UNICODE = "(*UCP)"


@dataclass(frozen=True)
class Match:
    job: CorpusJob
    # The green words found, in the title or anywhere in the text: why the job is on the page.
    words: tuple[str, ...]
    # Those of them in the title, which says more than the text does.
    in_title: tuple[str, ...]

    @property
    def rank(self) -> tuple[int, int]:
        """The more of their words a job has, the better it probably is; a title that names one
        breaks a tie (owner, 2026-11-02). Nothing is weighted or learned."""
        return len(self.words), len(self.in_title)


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


def found(regex: re.Pattern, text: str | None) -> set[str]:
    """The words of `regex` that `text` has, each as words are compared."""
    return {norm(hit) for hit in regex.findall(text or "")}


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


def has_any(words: list[str]) -> dict:
    """A query condition: the title or the description has one of these words, judged in the database."""
    regex = _UNICODE + pattern(words).pattern
    return {"$or": [{"title": {"$regex": regex, "$options": "i"}},
                    {"$expr": {"$regexMatch": {"input": {"$ifNull": ["$description", ""]}, "regex": regex,
                                               "options": "i"}}}]}


def _hits(field: str, words: list[str]) -> dict:
    """The words of `words` a field has, as the database finds them, each as it is written there."""
    if not words:
        return {"$literal": []}
    regex = _UNICODE + pattern(words).pattern
    found_there = {"$regexFindAll": {"input": {"$ifNull": [field, ""]}, "regex": regex, "options": "i"}}
    return {"$map": {"input": found_there, "in": "$$this.match"}}


def matching_jobs(db: Database, user: User, *, since: datetime | None = None,
                  described_since: datetime | None = None, scan: int = SCAN,
                  text: bool = False) -> list[Match]:
    """This person's matches, the most of their words first, then newest first. `since` keeps those
    found after it; `described_since` those that had their whole text by it, which is what alerts send.

    Each posting says which of everyone's words it has (`libs.corpus.terms`), so a match is an index
    lookup. A posting not yet searched, and a word nobody's postings have been searched for yet, are
    read with a regex over the whole text, so nothing waits on the index. Only the words come back,
    unless `text` asks for the description too, for a caller that shows it (an alert's salary line).
    """
    config = TenantRepository(db).get_config(user.id)
    mine = green_words(config)
    if not mine:
        return []

    filters = filters_for(db, user)
    where = candidate_conditions(filters)
    if since is not None:
        where.append({"first_seen_at": {"$gt": since}})
    if described_since is not None:
        where.append({"described_at": {"$gte": described_since}})
    indexed = sorted(db[Word.COLLECTION].distinct("_id", {"_id": {"$in": mine}, "indexed_at": {"$ne": None}}))
    pending = [word for word in mine if word not in indexed]
    unsearched = {"$eq": [{"$ifNull": ["$terms_at", None]}, None]}

    def hits(field: str) -> dict:
        return {"$cond": [unsearched, _hits(field, mine), _hits(field, pending)]}

    newest = [{"$sort": {"first_seen_at": -1, "_id": -1}}, {"$limit": scan}]
    found = [{"$addFields": {"_in_title": hits("$title"), "_in_text": hits("$description")}}]
    # A minimum salary is read off the text, so it comes back for someone who set one.
    if not (text or (config or {}).get("min_salary")):
        found.append({"$unset": "description"})
    # Separate questions, not one with an "or": the postings with a word already searched for, by the words
    # index; those not searched yet, by their text. Asked as one, the database read every posting with a
    # person's words and sorted them; asked apart, it reads the index newest first and stops at `scan`. At
    # 1.5 million postings, the busiest person's match read 4,996 postings in 1.2 s against 160,000 in over
    # two minutes (2026-11-04). At 5,000, the index read in 101–132 ms what a regex over the text read in
    # 439–482 ms (2026-11-02).
    searches = [[{"$match": {"$and": [*where, {"terms_at": None, **has_any(mine)}]}}, *newest, *found]]
    if indexed:
        searches.append([{"$match": {"$and": [*where, {"terms": {"$in": indexed}}]}}, *newest, *found])
    if pending:
        # A word not searched for yet (`libs.corpus.terms` does, within a run), read in the newest
        # candidates' text meanwhile. Only that word: when every word was read this way, adding one cut a
        # person's list from ~1,500 jobs to 458 until the run that searched for it (2026-11-04).
        searches.append([{"$match": {"$and": where}}, *newest, *found, {"$match": {"$or": [
            {"_in_title.0": {"$exists": True}}, {"_in_text.0": {"$exists": True}}]}}])
    rows = sorted({row["_id"]: row for stages in searches
                   for row in db[CorpusJob.COLLECTION].aggregate(stages)}.values(),
                  key=lambda row: (row["first_seen_at"], row["_id"]), reverse=True)[:scan]
    if (since or described_since) and len(rows) == scan:
        # The caller's watermark will pass every older match this never reached.
        logger.warning("matching: %s has at least %d new matches; only the newest %d were read, so older "
                       "ones will not be alerted (the jobs page still lists them)", user.id, scan, scan)

    mine_set, matches = set(mine), []
    for row in rows:
        searched = row.get("terms_at") is not None
        in_title = {norm(hit) for hit in row.pop("_in_title")} | (
            set(row.get("title_terms") or ()) & mine_set if searched else set())
        words = in_title | {norm(hit) for hit in row.pop("_in_text")} | (
            set(row.get("terms") or ()) & mine_set if searched else set())
        job = CorpusJob.from_doc(row)
        if (words and not filters.unwanted({name: getattr(job, name) for name in _ROW})
                and not underpaid(config, job.description)):
            matches.append(Match(job, tuple(sorted(words)), tuple(sorted(in_title))))
    # A stable sort, so equal ranks keep the query's newest-first order.
    return sorted(matches, key=lambda match: match.rank, reverse=True)


def marks(db: Database, user_id) -> tuple[set[int], dict[int, datetime]]:
    """The jobs this person skipped, and when they applied to which."""
    rows = list(db[UserJobState.COLLECTION].find(
        {"user_id": user_id, "$or": [{"dismissed_at": {"$ne": None}}, {"applied_at": {"$ne": None}}]},
        {"job_id": 1, "dismissed_at": 1, "applied_at": 1}))
    return ({row["job_id"] for row in rows if row["dismissed_at"]},
            {row["job_id"]: row["applied_at"] for row in rows if row["applied_at"]})


def their_jobs(db: Database, user: User, *, since: datetime | None = None) -> list[Match]:
    """What this person's Jobs page lists: their matches, less a job they skipped or applied to, which is
    gone from it for good."""
    skipped, applied = marks(db, user.id)
    return [match for match in matching_jobs(db, user, since=since)
            if match.job.id not in skipped and match.job.id not in applied]
