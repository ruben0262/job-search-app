"""Postings that have aged out of the corpus: written to a file, then deleted.

The rule: **a posting leaves whole or it does not leave.** It is written to an append-only archive
file first, and only deleted if that write succeeded. A posting somebody **acted on** — applied or
built a cover letter — never leaves, and that is asked of `user_job_state` itself rather than read
from a flag, because a derived answer cannot drift from the thing it describes (CLAUDE.md §5).

A **skipped** posting ages out like any other (owner, 2026-11-02): skipping says "never show me this",
and a posting that is gone is never shown. It used to be kept forever, and Skip is the mark people
use most, so most of the corpus would have been.

**`user_job_state` is drained by the same pass, under one rule**: a person's row survives only while
its posting does, or because it records something that person did (`MARKED`, a skip too). Deleting
the posting takes its rows with it; `forget_stale_state` handles the other half — rows other accounts
hold against a posting kept for somebody *else's* action. Without it the collection grows with
accounts × kept postings and never shrinks.

On Atlas's free tier this pass is also what keeps the platform inside 0.5 GB (ROADMAP Phase 9).
"""
from __future__ import annotations

import gzip
import json
import logging
import lzma
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from pymongo.database import Database

from libs.db import now
from libs.db.models import CorpusJob, UserJobState
from libs.settings import corpus_retention_days

logger = logging.getLogger(__name__)

# Where the archive is written. An env var for the same reason `LOG_DIR` is one: the path differs
# between the box and a laptop, and nothing else about the behaviour does.
DEFAULT_ARCHIVE_DIR = Path(os.environ.get("ARCHIVE_DIR") or "archive")

# How many rows one pass moves. The pass runs on the harvest tick, so this is a ceiling on the work
# added to a cycle that already has an 11-minute budget, not a limit on how much can ever be
# archived — the next tick continues where this one stopped. Measured 2026-11-05 at 400,000 postings:
# 5,000 a pass took 8.3 s, 480,000 a day at a pass a tick; the EU's and the UK's ~70,000 a day leave
# within hours of their day, and a keep shortened from 30 days to 15 is caught up in about 10 hours.
ARCHIVE_BATCH = 5000

@dataclass(frozen=True)
class ArchiveResult:
    """What one pass moved out."""

    jobs: int
    states: int
    path: Path | None


# A row whose posting is kept forever: the person applied, or built a cover letter for it.
ACTED_ON = {"$or": [{"applied_at": {"$ne": None}}, {"cover_built": True}]}
# A row recording something the person did, rather than something we worked out: kept while its posting
# is. A skip is one, so a posting kept for somebody else's application stays skipped for this person.
MARKED = {"$or": [*ACTED_ON["$or"], {"dismissed_at": {"$ne": None}}]}


def _plain(value: object) -> object:
    """JSON cannot hold a datetime, a UUID or bytes, and an archive nobody can read is not one."""
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).hex()
    raise TypeError(f"no archive representation for {type(value).__name__}")


def archive_expired(db: Database, *, retention_days: int | None = None,
                    directory: Path | None = None,
                    limit: int = ARCHIVE_BATCH) -> ArchiveResult:
    """Move postings older than `retention_days` — the operator's setting unless given — out of the
    corpus and into the archive file.

    The order is deliberate: **write, then delete.** If the write raises, nothing is deleted and the
    next pass tries the same rows again. The archive is append-only and a crash between the write
    and the delete can repeat a row in it — a duplicate line is recoverable and a missing posting is
    not, so the trade is taken in that direction on purpose.
    """
    if retention_days is None:
        retention_days = corpus_retention_days(db)
    cutoff = now() - timedelta(days=retention_days)
    kept = db[UserJobState.COLLECTION].distinct("job_id", ACTED_ON)
    rows = list(db[CorpusJob.COLLECTION].find(_due(cutoff, kept), sort=[("first_seen_at", 1)], limit=limit))
    ids = [row["_id"] for row in rows]

    path: Path | None = None
    states = 0
    if ids:
        path = write_archive([{"id": row.pop("_id"), **row} for row in rows], directory)
        # Nobody applied to these postings, so every state row for them goes with them: a skip too,
        # since there is nothing left to show.
        states = db[UserJobState.COLLECTION].delete_many({"job_id": {"$in": ids}}).deleted_count
        db[CorpusJob.COLLECTION].delete_many({"_id": {"$in": ids}})

    states += forget_stale_state(db, cutoff=cutoff, kept=kept, limit=limit)
    if ids or states:
        logger.info("archive: %d posting(s) and %d user state row(s) removed, older than %d days%s",
                    len(ids), states, retention_days,
                    f", archived to {path.name}" if path else "")
    return ArchiveResult(len(ids), states, path)


def _due(cutoff: datetime, kept: list[int]) -> dict:
    """The postings the pass moves: found before `cutoff`, and nobody applied to them or wrote them a
    cover letter."""
    return {"first_seen_at": {"$lt": cutoff}, "_id": {"$nin": kept}}


def overdue(db: Database) -> int:
    """Postings still here a day after the pass should have moved them: it is behind, or failing. A
    pass a tick moves up to `ARCHIVE_BATCH`, so a working one leaves none this old."""
    cutoff = now() - timedelta(days=corpus_retention_days(db) + 1)
    return db[CorpusJob.COLLECTION].count_documents(
        _due(cutoff, db[UserJobState.COLLECTION].distinct("job_id", ACTED_ON)))


def forget_stale_state(db: Database, *, cutoff: datetime, kept: list[int] | None = None,
                       limit: int = ARCHIVE_BATCH) -> int:
    """Drop the rows other accounts hold against old postings kept for somebody else's action.

    A posting one account applied to stays in the corpus, correctly. Every *other* account's row for
    it only says we sent it, and those rows would never have a reason to leave. Bounded like the rest
    of the pass, so it cannot be the stage that overruns the harvest tick.
    """
    kept = db[UserJobState.COLLECTION].distinct("job_id", ACTED_ON) if kept is None else kept
    old = db[CorpusJob.COLLECTION].distinct("_id", {"_id": {"$in": kept}, "first_seen_at": {"$lt": cutoff}})
    stale = [row["_id"] for row in db[UserJobState.COLLECTION].find(
        {"job_id": {"$in": old}, "$nor": [MARKED]}, {"_id": 1}, limit=limit)] if old else []
    if not stale:
        return 0
    # Asked again on delete: a person who acted between the read and the delete keeps their row.
    return db[UserJobState.COLLECTION].delete_many({"_id": {"$in": stale}, "$nor": [MARKED]}).deleted_count


# How hard the archive is compressed. Measured 2026-11-04 on 1,000 LinkedIn postings (5.1 MB as JSON):
# gzip at its best, 1.45 MB in 0.6 s; xz at 6, 0.95 MB in 5 s, once a tick; xz at its best, 0.94 MB in
# 6 s. The archive is kept forever (owner), and is ~70,000 postings a day once every country is read.
ARCHIVE_PRESET = 6


def write_archive(rows: list, directory: Path | None = None) -> Path:
    """Append rows to today's archive file, one JSON object per line, xz-compressed."""
    target = Path(directory) if directory is not None else DEFAULT_ARCHIVE_DIR
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"corpus-{datetime.now(UTC):%Y-%m-%d}.jsonl.xz"

    # Append rather than replace: a second pass on the same day adds a stream to the same file, and
    # readers see the streams as one.
    with lzma.open(path, "at", encoding="utf-8", preset=ARCHIVE_PRESET) as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), default=_plain, ensure_ascii=False) + "\n")
    return path


def read_archive(path: Path) -> list[dict]:
    """Read one archive file back. The proof that what was written can be recovered. Files written
    before 2026-11-04 are gzip (`.gz`)."""
    opener = gzip.open if Path(path).suffix == ".gz" else lzma.open
    with opener(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]
