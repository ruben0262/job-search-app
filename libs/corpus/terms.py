"""Which of everyone's green words each posting has, kept on the posting, so finding a person's jobs is
an index lookup rather than a regex over every description they could see (owner, 2026-11-02).

One writer, `catch_up`, which the harvester runs after each pass:

- `vocabulary` holds every green word the postings have been searched for.
- A posting's `terms` are the vocabulary's words it has, anywhere; `title_terms` the ones in its title;
  `terms_at` when they were found. A new posting has no `terms_at`, and new text clears it
  (`CorpusRepository.set_description`).
- A word an account has and the vocabulary does not is looked for in the postings that already have
  their terms, then added to the vocabulary. A posting without `terms_at` has every word looked for.

Nothing waits on it to be found: `libs.matching` reads a posting without `terms_at`, and a word not yet
in the vocabulary, the way it always did, with a regex over the text.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache

from pymongo import UpdateOne
from pymongo.database import Database

from libs.corpus.geo import posting_country, read_city
from libs.corpus.repository import MIN_DESCRIPTION
from libs.db import now
from libs.db.models import CorpusJob, UserConfig, Word
from libs.matching import green_words, has_any, norm
from libs.obs import audit

logger = logging.getLogger(__name__)

# Postings written per round trip.
BATCH = 500


@dataclass
class CatchUp:
    """What one pass did."""

    new_words: int = 0
    # Postings that already had their terms and turned out to have one of the new words.
    backfilled: int = 0
    # Postings whose words were found for the first time, or again after new text.
    indexed: int = 0


@cache
def _bounded(word: str) -> re.Pattern:
    return re.compile(rf"(?<!\w){re.escape(word)}(?!\w)")


def found(text: str | None, words: Iterable[str]) -> set[str]:
    """The words a text has, as `libs.matching.pattern` finds them: whole words, any case, a space and
    a hyphen alike. Each word is looked for on its own, so "engineer" is found inside "backend
    engineer" too, which one regex of every word would have passed over."""
    hay = norm(text or "")
    return {word for word in words if word in hay and _bounded(word).search(hay)}


def terms_of(title: str | None, description: str | None, words: Iterable[str]) -> dict[str, list[str]]:
    words = list(words)
    in_title = found(title, words)
    return {"terms": sorted(in_title | found(description, words)), "title_terms": sorted(in_title)}


def catch_up(db: Database) -> CatchUp:
    """Look for every account's new words in the postings, and every word in the new postings."""
    report = CatchUp()
    started = now()
    known = set(db[Word.COLLECTION].distinct("_id"))
    wanted = {word for row in db[UserConfig.COLLECTION].find({}, {"config": 1})
              for word in green_words(row.get("config"))}
    new = sorted(wanted - known)
    if new:
        report.new_words = len(new)
        report.backfilled = _backfill(db, new)
    report.indexed = _index(db, sorted(known | set(new)), at=started)
    if new:
        # Only now: until both passes are done, matching reads these words with a regex.
        db[Word.COLLECTION].bulk_write([UpdateOne({"_id": word}, {"$setOnInsert": {"indexed_at": now()}},
                                                  upsert=True) for word in new], ordered=False)
    if report.new_words or report.indexed:
        logger.info("terms: %d new word(s) found in %d posting(s); %d posting(s) indexed",
                    report.new_words, report.backfilled, report.indexed)
    audit(logger, "terms.catch_up", new_words=report.new_words, backfilled=report.backfilled,
          indexed=report.indexed)
    return report


def _backfill(db: Database, words: list[str]) -> int:
    """The new words, in the postings that already have their terms. The database picks out the ones
    with any of them, so only those cross the network."""
    corpus = db[CorpusJob.COLLECTION]
    writes = []
    for row in corpus.find({"terms_at": {"$ne": None}, **has_any(words)}, {"title": 1, "description": 1}):
        terms = terms_of(row.get("title"), row.get("description"), words)
        if terms["terms"]:
            writes.append(UpdateOne({"_id": row["_id"]}, {"$addToSet": {
                "terms": {"$each": terms["terms"]}, "title_terms": {"$each": terms["title_terms"]}}}))
    for start in range(0, len(writes), BATCH):
        corpus.bulk_write(writes[start:start + BATCH], ordered=False)
    return len(writes)


def _index(db: Database, words: list[str], *, at) -> int:
    """Every word, in each posting that has no terms yet. Written only if its text is still the text
    read, so a description stored meanwhile is indexed next time rather than marked done."""
    corpus = db[CorpusJob.COLLECTION]
    writes, count = [], 0
    for row in corpus.find({"terms_at": None}, {"title": 1, "description": 1, "description_fetched_at": 1,
                                                "location": 1, "city": 1, "country": 1, "described_at": 1,
                                                "first_seen_at": 1}):
        values = {**terms_of(row.get("title"), row.get("description"), words), "terms_at": at}
        # Postings stored before a city and a described time were kept: given theirs once, here.
        if "city" not in row:
            values["city"] = read_city(row.get("location"))
        # And a country, for those stored before a city anywhere in the world could give one.
        if row.get("country") is None:
            values["country"] = posting_country(row.get("location"), title=row.get("title"))
        fetched = row.get("description_fetched_at")
        if "described_at" not in row:
            whole = len(row.get("description") or "") >= MIN_DESCRIPTION
            values["described_at"] = (fetched or row["first_seen_at"]) if whole else None
        writes.append(UpdateOne({"_id": row["_id"], "description_fetched_at": fetched}, {"$set": values}))
        if len(writes) == BATCH:
            count += corpus.bulk_write(writes, ordered=False).modified_count
            writes = []
    if writes:
        count += corpus.bulk_write(writes, ordered=False).modified_count
    return count
