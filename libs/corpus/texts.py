"""A posting's text, kept beside it (`CorpusText`) under its id rather than in it. What reads a text asks
for it here, by the postings' ids; what writes one writes it here.

The Jobs list reads postings and never their text, and with the text inside each posting the database held
it in memory all the same: 4,006 of a posting's 6,857 bytes (2026-11-24)."""
from __future__ import annotations

import logging
from collections.abc import Iterable
from time import monotonic

from pymongo import ReplaceOne
from pymongo.database import Database

from libs.db.models import CorpusJob, CorpusText

logger = logging.getLogger(__name__)

# How many postings the deploy moves the text of at a time (`move_once`).
_MOVED_AT_ONCE = 2_000

# Stages that bring a posting's text into an aggregation over postings, as `description`, for a question that
# reads it there (a word not indexed yet, `libs.matching`): one lookup by id a posting, on the few it reaches.
JOINED: list[dict] = [
    {"$lookup": {"from": CorpusText.COLLECTION, "localField": "_id", "foreignField": "_id", "as": "_text"}},
    {"$set": {"description": {"$first": "$_text.description"}}},
    {"$unset": "_text"},
]


def texts(db: Database, ids: Iterable[int]) -> dict[int, str]:
    """The text of each of these postings that has one, by id."""
    if not (ids := list(dict.fromkeys(ids))):
        return {}
    return {row["_id"]: row["description"] for row in db[CorpusText.COLLECTION].find({"_id": {"$in": ids}})}


def with_texts[J: CorpusJob](db: Database, jobs: Iterable[J]) -> list[J]:
    """These postings, each carrying its text, for a reader that uses `CorpusJob.description`."""
    jobs = list(jobs)
    found = texts(db, (job.id for job in jobs))
    for job in jobs:
        job.description = found.get(job.id)
    return jobs


def store(db: Database, job_id: int, description: str) -> None:
    """A posting's text, replacing what it had."""
    db[CorpusText.COLLECTION].replace_one({"_id": job_id}, {"description": description}, upsert=True)


def forget(db: Database, ids: Iterable[int]) -> None:
    """The texts of postings that are gone (`libs.corpus.archive`)."""
    if ids := list(ids):
        db[CorpusText.COLLECTION].delete_many({"_id": {"$in": ids}})


def move_once(db: Database) -> int:
    """Every text still inside its posting, moved beside it, a batch at a time; how many postings. Safe to
    repeat and to stop: a posting gives up its text only once the text is stored beside it. The deploy runs it
    (`python -m libs.db`)."""
    corpus, moved, started = db[CorpusJob.COLLECTION], 0, monotonic()
    still_inside = {"description": {"$exists": True}}
    while rows := list(corpus.find(still_inside, {"description": 1}).limit(_MOVED_AT_ONCE)):
        if writes := [ReplaceOne({"_id": row["_id"]}, {"description": row["description"]}, upsert=True)
                      for row in rows if row.get("description")]:
            db[CorpusText.COLLECTION].bulk_write(writes, ordered=False)
        corpus.update_many({"_id": {"$in": [row["_id"] for row in rows]}}, {"$unset": {"description": ""}})
        moved += len(rows)
    if moved:
        logger.info("corpus: %d posting(s) gave their text to %s in %.1f s", moved, CorpusText.COLLECTION,
                    monotonic() - started)
    return moved
