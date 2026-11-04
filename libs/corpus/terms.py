"""Which of everyone's green words each posting has, kept on the posting, so finding a person's jobs is
an index lookup rather than a regex over every description they could see (owner, 2026-11-02).

One writer, `catch_up`, which the harvester runs after each pass:

- `vocabulary` holds every green word anybody has; `indexed_at` says the postings that can still be
  shown have all been searched for it.
- A posting's `terms` are the vocabulary's words it has, anywhere; `title_terms` the ones in its title;
  `terms_at` when they were found. A new posting has no `terms_at`, and new text clears it
  (`CorpusRepository.set_description`). A posting without `terms_at` has every word looked for.
- A word an account has and the vocabulary does not joins it at once, and is looked for in the postings
  already indexed, newest first, a stretch each run until it reaches the oldest that can be shown.

Nothing waits on it to be found: `libs.matching` reads a posting without `terms_at`, and a word not yet
indexed, the way it always did, with a regex over the text.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from functools import cache
from time import monotonic

from pymongo import UpdateOne
from pymongo.database import Database

from libs.corpus.geo import posting_country, read_city
from libs.corpus.repository import MIN_DESCRIPTION
from libs.db import now
from libs.db.models import CorpusJob, UserConfig, Word
from libs.matching import green_words, has_any, norm
from libs.obs import audit
from libs.settings import TERMS_BUDGET_SECONDS
from libs.tenant.filters import candidate_since, platform_windows

logger = logging.getLogger(__name__)

# Postings written per round trip.
BATCH = 500

# How much of the store, by when its postings were found, a new word is searched for at a time: where it
# got to is kept after each, so a run that runs out of time loses none of it.
STRETCH = timedelta(hours=6)


@dataclass
class CatchUp:
    """What one pass did."""

    new_words: int = 0
    # Postings that already had their terms and turned out to have a word being searched for.
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


def catch_up(db: Database, *, budget_seconds: float = TERMS_BUDGET_SECONDS) -> CatchUp:
    """Look for every word in the new postings, and for each account's new words in the postings that
    can still be shown, a stretch at a time, for at most `budget_seconds`."""
    report = CatchUp()
    started, deadline = now(), monotonic() + budget_seconds
    vocabulary = {row["_id"]: row for row in db[Word.COLLECTION].find({})}
    wanted = {word for row in db[UserConfig.COLLECTION].find({}, {"config": 1})
              for word in green_words(row.get("config"))}
    if new := sorted(wanted - set(vocabulary)):
        # Known at once, so the postings indexed below have them too; searched for in the rest from here.
        db[Word.COLLECTION].bulk_write([UpdateOne({"_id": word}, {"$setOnInsert": {
            "indexed_at": None, "searched_to": None}}, upsert=True) for word in new], ordered=False)
        vocabulary.update({word: {"indexed_at": None, "searched_to": None} for word in new})
    report.new_words = len(new)
    report.indexed = _index(db, sorted(vocabulary), at=started)
    windows = platform_windows(db)
    since = candidate_since(windows.max_age_hours, windows.source_min_ages, now=started)
    for word, row in sorted(vocabulary.items()):
        if row.get("indexed_at") is None:
            report.backfilled += _backfill(db, word, since=since, upto=row.get("searched_to"),
                                           deadline=deadline)
    if report.new_words or report.indexed or report.backfilled:
        logger.info("terms: %d new word(s); %d posting(s) indexed; %d found to have a word being searched "
                    "for", report.new_words, report.indexed, report.backfilled)
    audit(logger, "terms.catch_up", new_words=report.new_words, backfilled=report.backfilled,
          indexed=report.indexed)
    return report


def _backfill(db: Database, word: str, *, since, upto, deadline: float) -> int:
    """One word, in the postings already indexed without it, newest first, a stretch of `STRETCH` at a
    time, back to `since`: older ones are never shown (`filters.candidate_since`). At least one stretch a
    run, however little time is left, so a word is never starved. Where it got to is kept on the word, so
    a run out of time goes on from there next run; at `since` it is indexed, and matching reads it from
    the index. The database picks out the postings with the word, so only those cross the network."""
    corpus, words, found_in = db[CorpusJob.COLLECTION], [word], 0
    upto = upto or now()
    while upto > since:
        start = max(since, upto - STRETCH)
        writes = []
        for row in corpus.find({"closed_at": None, "terms_at": {"$ne": None},
                                "first_seen_at": {"$gte": start, "$lt": upto}, **has_any(words)},
                               {"title": 1, "description": 1}):
            terms = terms_of(row.get("title"), row.get("description"), words)
            if terms["terms"]:
                writes.append(UpdateOne({"_id": row["_id"]}, {"$addToSet": {
                    "terms": {"$each": terms["terms"]}, "title_terms": {"$each": terms["title_terms"]}}}))
        for at in range(0, len(writes), BATCH):
            corpus.bulk_write(writes[at:at + BATCH], ordered=False)
        found_in += len(writes)
        upto = start
        db[Word.COLLECTION].update_one({"_id": word}, {"$set": {"searched_to": upto}})
        if upto > since and monotonic() >= deadline:
            logger.info("terms: '%s' searched back to %s; the next run goes on from there", word, upto)
            return found_in
    db[Word.COLLECTION].update_one({"_id": word}, {"$set": {"indexed_at": now()}})
    return found_in


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
