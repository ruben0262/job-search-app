"""Which of everyone's green words each posting has, kept on the posting, so finding a person's jobs is
an index lookup rather than a regex over every description they could see (owner, 2026-11-02).

- `vocabulary` holds every green word anybody has; `indexed_at` says the postings that can still be
  shown have all been searched for it.
- A posting's `terms` are the vocabulary's words it has, anywhere; `title_terms` the ones in its title;
  `terms_at` when they were found: in the same write that stores its text
  (`CorpusRepository.set_description`), so a job's words are found the moment its text is read. Found at
  the end of each run before 2026-11-06, a text read just after one waited up to 15 minutes, 689 of them
  at once, and the Jobs page read every one with a regex meanwhile: 171 ms with 2,000 waiting, 84 with none.
- `catch_up`, which the harvester runs after each pass, keeps the vocabulary: a word an account has and
  the vocabulary does not joins it, and is looked for in the postings already indexed, newest first, a
  stretch each run until it reaches the oldest that can be shown.

Nothing waits on it to be found: `libs.matching` reads a posting without `terms_at`, and a word not yet
indexed, the way it always did, with a regex over the text.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from time import monotonic

from pymongo import UpdateOne
from pymongo.database import Database

from libs.corpus.freshness import candidate_since, platform_windows
from libs.corpus.words import terms_of
from libs.db import now
from libs.db.models import CorpusJob, UserConfig, Word
from libs.matching import green_words, has_any
from libs.obs import audit
from libs.settings import TERMS_BUDGET_SECONDS

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


def catch_up(db: Database, *, budget_seconds: float = TERMS_BUDGET_SECONDS) -> CatchUp:
    """Each account's new words into the vocabulary, and looked for in the postings that can still be
    shown, a stretch at a time, for at most `budget_seconds`."""
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
    windows = platform_windows(db)
    since = candidate_since(windows.max_age_hours, windows.source_min_ages, now=started)
    for word, row in sorted(vocabulary.items()):
        if row.get("indexed_at") is None:
            report.backfilled += _backfill(db, word, since=since, upto=row.get("searched_to"),
                                           deadline=deadline)
    if report.new_words or report.backfilled:
        logger.info("terms: %d new word(s); %d posting(s) found to have a word being searched for",
                    report.new_words, report.backfilled)
    audit(logger, "terms.catch_up", new_words=report.new_words, backfilled=report.backfilled)
    return report


def _backfill(db: Database, word: str, *, since, upto, deadline: float) -> int:
    """One word, in the postings already indexed without it, newest first, a stretch of `STRETCH` at a
    time, back to `since`: older ones are never shown (`freshness.candidate_since`). At least one stretch a
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
