"""Postings that have aged out of the corpus: written to a file, then deleted.

The rule: **a posting leaves whole or it does not leave.** It is written to an append-only archive
file first, and only deleted if that write succeeded. A posting somebody **acted on** — applied, built
a cover letter, or hid — never leaves, and that is asked of `user_job_state` itself rather than read
from a flag, because a derived answer cannot drift from the thing it describes (CLAUDE.md §5).

**`user_job_state` is drained by the same pass, under one rule**: a person's row survives only while
its posting does, or because it records something that person did. Deleting the posting takes its
rows with it; `forget_stale_state` handles the other half — rows other accounts hold against a
posting kept for somebody *else's* action. Without it the collection grows with accounts × kept
postings and never shrinks.

On Atlas's free tier this pass is also what keeps the platform inside 0.5 GB (ROADMAP Phase 9).
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from pymongo.database import Database

from libs.db import now
from libs.db.models import CorpusJob, UserJobState
from libs.settings import CORPUS_RETENTION_DAYS

logger = logging.getLogger(__name__)

# Where the archive is written. An env var for the same reason `LOG_DIR` is one: the path differs
# between the box and a laptop, and nothing else about the behaviour does.
DEFAULT_ARCHIVE_DIR = Path(os.environ.get("ARCHIVE_DIR") or "archive")

# How many rows one pass moves. The pass runs on the harvest tick, so this is a ceiling on the work
# added to a cycle that already has an 11-minute budget, not a limit on how much can ever be
# archived — the next tick continues where this one stopped.
ARCHIVE_BATCH = 1000

@dataclass(frozen=True)
class ArchiveResult:
    """What one pass moved out."""

    jobs: int
    states: int
    path: Path | None


# A row recording something the person did, rather than something we worked out.
ACTED_ON = {"$or": [{"applied_at": {"$ne": None}}, {"cover_built": True}, {"dismissed_at": {"$ne": None}}]}


def _plain(value: object) -> object:
    """JSON cannot hold a datetime, a UUID or bytes, and an archive nobody can read is not one."""
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).hex()
    raise TypeError(f"no archive representation for {type(value).__name__}")


def archive_expired(db: Database, *, retention_days: int = CORPUS_RETENTION_DAYS,
                    directory: Path | None = None,
                    limit: int = ARCHIVE_BATCH) -> ArchiveResult:
    """Move postings older than `retention_days` out of the corpus and into the archive file.

    The order is deliberate: **write, then delete.** If the write raises, nothing is deleted and the
    next pass tries the same rows again. The archive is append-only and a crash between the write
    and the delete can repeat a row in it — a duplicate line is recoverable and a missing posting is
    not, so the trade is taken in that direction on purpose.
    """
    cutoff = now() - timedelta(days=retention_days)
    kept = db[UserJobState.COLLECTION].distinct("job_id", ACTED_ON)
    rows = list(db[CorpusJob.COLLECTION].find({"first_seen_at": {"$lt": cutoff}, "_id": {"$nin": kept}},
                                              sort=[("first_seen_at", 1)], limit=limit))
    ids = [row["_id"] for row in rows]

    path: Path | None = None
    states = 0
    if ids:
        path = write_archive([{"id": row.pop("_id"), **row} for row in rows], directory)
        # Nobody acted on these postings, so every state row for them is ours to drop.
        states = db[UserJobState.COLLECTION].delete_many({"job_id": {"$in": ids}}).deleted_count
        db[CorpusJob.COLLECTION].delete_many({"_id": {"$in": ids}})

    states += forget_stale_state(db, cutoff=cutoff, kept=kept)
    if ids or states:
        logger.info("archive: %d posting(s) and %d user state row(s) removed, older than %d days%s",
                    len(ids), states, retention_days,
                    f", archived to {path.name}" if path else "")
    return ArchiveResult(len(ids), states, path)


def forget_stale_state(db: Database, *, cutoff: datetime, kept: list[int] | None = None) -> int:
    """Drop the rows other accounts hold against old postings kept for somebody else's action.

    A posting one account applied to stays in the corpus, correctly. Every *other* account's row for
    it only says we sent it, and those rows would never have a reason to leave.
    """
    kept = db[UserJobState.COLLECTION].distinct("job_id", ACTED_ON) if kept is None else kept
    old = db[CorpusJob.COLLECTION].distinct("_id", {"_id": {"$in": kept}, "first_seen_at": {"$lt": cutoff}})
    if not old:
        return 0
    return db[UserJobState.COLLECTION].delete_many(
        {"job_id": {"$in": old}, "$nor": [ACTED_ON]}).deleted_count


def write_archive(rows: list, directory: Path | None = None) -> Path:
    """Append rows to today's archive file, one JSON object per line, gzipped."""
    target = Path(directory) if directory is not None else DEFAULT_ARCHIVE_DIR
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"corpus-{datetime.now(UTC):%Y-%m-%d}.jsonl.gz"

    # Append rather than replace: a second pass on the same day adds to the same file, and `at`
    # against a gzip member boundary is valid — readers see one continuous stream.
    with gzip.open(path, "at", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), default=_plain, ensure_ascii=False) + "\n")
    return path


def read_archive(path: Path) -> list[dict]:
    """Read one archive file back. The proof that what was written can be recovered."""
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]
