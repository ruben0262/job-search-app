"""Postings that have aged out of the corpus: written to a file, then deleted.

**Replaces `trim_cold`, which nulled the description and vector in place and left the row.** That
was data loss wearing housekeeping's clothes, and it was worse than it looked: the trainer rebuilds
every training set by joining `user_labels` back to the corpus row and reading its embedding
(`services/trainer/train.py`), with `WHERE embedding IS NOT NULL`. A trimmed row therefore dropped
out of the next retrain silently — no error, just a smaller training set and a worse model.

The rule here instead: **a row leaves whole or it does not leave.** It is written to an append-only
archive file first, and only deleted if that write succeeded.

Two kinds of row never leave at all, and neither is decided by a flag:

- anything carrying a **label**, because that is somebody's training data;
- anything a **person acted on** — applied, built a cover letter, or dismissed.

Asked as `NOT EXISTS` against the real tables rather than read from a denormalised `pinned` column,
which is what `pinned` was and why it is gone. Two of the three places that write a label never set
it, so the majority of every training set sat on rows the old code considered disposable. A derived
answer cannot drift from the thing it describes; a copy always can (CLAUDE.md §5).

**`user_job_state` is drained by the same pass, and one rule covers both halves**: a row survives
only while its posting does, or because it records something that account's own user did. Deleting
the posting takes its state rows with it; `forget_stale_state` handles the other half, the score rows
that other accounts hold against a posting kept for somebody *else's* label. Without that second
half the table grows with accounts × kept postings and never shrinks.
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

from sqlalchemy import delete, exists, inspect, or_, select, tuple_
from sqlalchemy.orm import Session

from libs.db.models import CorpusJob, UserJobState, UserLabel
from libs.settings import CORPUS_RETENTION_DAYS

logger = logging.getLogger(__name__)

# Where the archive is written. An env var for the same reason `LOG_DIR` is one: the path differs
# between the box and a laptop, and nothing else about the behaviour does.
DEFAULT_ARCHIVE_DIR = Path(os.environ.get("ARCHIVE_DIR") or "archive")

# How many rows one pass moves. The pass runs on the harvest tick, so this is a ceiling on the work
# added to a cycle that already has an 11-minute budget, not a limit on how much can ever be
# archived — the next tick continues where this one stopped.
ARCHIVE_BATCH = 1000

# The one column the archive does not keep, and the reason is size: the vectors are the bulk of the
# 386 MB the corpus reached in a fortnight, and a vector is reproducible from the description that
# is kept. Everything else about the posting is written, including its text.
EXCLUDED_FROM_ARCHIVE = frozenset({"embedding"})


@dataclass(frozen=True)
class ArchiveResult:
    """What one pass moved out."""

    jobs: int
    states: int
    path: Path | None


def _acted_on():
    """A state row recording something the person did, rather than something we computed."""
    return or_(UserJobState.applied_at.is_not(None),
               UserJobState.cover_built.is_(True),
               UserJobState.dismissed_at.is_not(None))


def _archivable(cutoff: datetime):
    """The conditions a row must meet to leave. Read this as the promise the archive makes."""
    labelled = exists(select(1).where(UserLabel.job_id == CorpusJob.id))
    acted_on = exists(select(1).where(UserJobState.job_id == CorpusJob.id, _acted_on()))
    return (CorpusJob.first_seen_at < cutoff, ~labelled, ~acted_on)


def _state_worth_keeping():
    """Whether one account's row for one posting is that person's history rather than our working out.

    `score`, `scored_at`, `notified_at` and `retired_at` are all things the platform computed and can
    compute again. What cannot be recovered is what the person did: applied, built a cover letter,
    dismissed it, or taught the model something by labelling it.

    Note it is **that user's own** label, not anybody's. A posting one account labelled stays in the
    corpus for everyone, and without this every other account's score row for it would stay forever
    too — which is how `user_job_state` grows with accounts × labelled postings rather than with
    either alone.
    """
    own_label = exists(select(1).where(UserLabel.user_id == UserJobState.user_id,
                                       UserLabel.job_id == UserJobState.job_id))
    return or_(_acted_on(), own_label)


def _archived_columns() -> list:
    """Every column except the excluded ones, taken from the mapper rather than listed here.

    Listed by hand, this is one more place to forget: a column added to `CorpusJob` would be dropped
    by the archive silently, which is exactly the loss this module exists to prevent.
    """
    return [c for c in inspect(CorpusJob).columns if c.key not in EXCLUDED_FROM_ARCHIVE]


def _plain(value: object) -> object:
    """JSON cannot hold a datetime, a UUID or bytes, and an archive nobody can read is not one."""
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).hex()
    raise TypeError(f"no archive representation for {type(value).__name__}")


def archive_expired(session: Session, *, retention_days: int = CORPUS_RETENTION_DAYS,
                    directory: Path | None = None,
                    limit: int = ARCHIVE_BATCH) -> ArchiveResult:
    """Move postings older than `retention_days` out of the corpus and into the archive file.

    The order is deliberate: **write, then delete.** If the write raises, nothing is deleted and the
    next pass tries the same rows again. The archive is append-only and a crash between the write
    and the commit can repeat a row in it — a duplicate line is recoverable and a missing posting is
    not, so the trade is taken in that direction on purpose.
    """
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    ids = list(session.scalars(
        select(CorpusJob.id)
        .where(*_archivable(cutoff))
        .order_by(CorpusJob.first_seen_at)
        .limit(limit)))

    path: Path | None = None
    states = 0
    if ids:
        rows = session.execute(
            select(*_archived_columns()).where(CorpusJob.id.in_(ids))).mappings().all()
        path = write_archive(rows, directory)

        # Unconditional, unlike the sweep below, and both facts matter. The conditions above already
        # guarantee no row here records anything a person did, so filtering again would be
        # redundant — and leaving even one behind makes the corpus delete fail on the RESTRICT.
        states = session.execute(
            delete(UserJobState).where(UserJobState.job_id.in_(ids))).rowcount or 0
        session.execute(delete(CorpusJob).where(CorpusJob.id.in_(ids)))

    states += forget_stale_state(session, cutoff=cutoff, limit=limit)
    if ids or states:
        logger.info("archive: %d posting(s) and %d user state row(s) removed, older than %d days%s",
                    len(ids), states, retention_days,
                    f", archived to {path.name}" if path else "")
    return ArchiveResult(len(ids), states, path)


def forget_stale_state(session: Session, *, cutoff: datetime, limit: int = ARCHIVE_BATCH) -> int:
    """Drop score rows for old postings that stayed, on accounts that did nothing with them.

    **Why this is not covered by archiving the posting.** A posting one account labelled or applied to
    stays in the corpus permanently — correctly. But every *other* account's row for it is a score we
    computed, and those rows never had a reason to leave. That is what made `user_job_state` grow with
    accounts × kept postings rather than with the retention window: 88,041 rows for two accounts in a
    fortnight, and nothing draining it (ROADMAP 6bd).

    Nothing is archived here because nothing is lost: a score is reproducible from the posting and the
    model, which is exactly what distinguishes it from the posting itself.
    """
    stale = (
        select(UserJobState.user_id, UserJobState.job_id)
        .join(CorpusJob, CorpusJob.id == UserJobState.job_id)
        .where(CorpusJob.first_seen_at < cutoff, ~_state_worth_keeping())
        .limit(limit))
    return session.execute(
        delete(UserJobState)
        .where(tuple_(UserJobState.user_id, UserJobState.job_id).in_(stale))).rowcount or 0


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
