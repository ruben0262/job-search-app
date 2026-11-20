"""Nightly backup: every collection worth keeping, as one gzipped file, read back before it counts.

What is kept is declared beside each collection (`Document.BACKED_UP`), so a new collection is in the
backup unless somebody decides otherwise. Postings are not kept — they are collected again within
hours, and on Atlas's free tier a nightly copy of them would spend a third of the week's transfer
allowance — except the ones somebody acted on, which their applied and hidden marks point at.

One line per document: `{"c": collection, "d": document}` in canonical extended JSON, which keeps
dates, UUIDs and bytes exactly. `restore.py` reads it back.
"""
from __future__ import annotations

import argparse
import gzip
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bson import json_util
from bson.binary import UuidRepresentation

from libs.corpus.archive import MARKED
from libs.db import BACKUP_KEEP_DAYS, database
from libs.db.models import MODELS, CorpusJob, User, UserJobState

# Account ids are UUIDs; without saying how to write one, the first account fails the whole backup.
OPTIONS = json_util.CANONICAL_JSON_OPTIONS.with_options(uuid_representation=UuidRepresentation.STANDARD)
# The integer ids handed out so far. Kept so a restore never hands one out twice: an old alert's
# link names a posting by its id.
COUNTERS = "counters"


def kept() -> list[str]:
    """The collections a backup holds in full."""
    return [model.COLLECTION for model in MODELS if model.BACKED_UP]


def write(db, target: Path) -> dict[str, int]:
    """Every kept collection, plus the postings somebody acted on. Returns the count per collection."""
    counts: dict[str, int] = {}
    with gzip.open(target, "wt", encoding="utf-8") as sink:
        def put(name: str, docs) -> None:
            for doc in docs:
                sink.write(json_util.dumps({"c": name, "d": doc}, json_options=OPTIONS) + "\n")
                counts[name] = counts.get(name, 0) + 1
        for name in kept():
            put(name, db[name].find())
        put(COUNTERS, db[COUNTERS].find())
        put(CorpusJob.COLLECTION, db[CorpusJob.COLLECTION].find(
            {"_id": {"$in": db[UserJobState.COLLECTION].distinct("job_id", MARKED)}}))
    return counts


def read(path: Path):
    """`(collection, document)` for every line of a backup."""
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                entry = json_util.loads(line, json_options=OPTIONS)
                yield entry["c"], entry["d"]


def verify(path: Path) -> bool:
    """Read the whole file back into the collections' own classes. An unverified backup is a hope."""
    if not path.exists() or path.stat().st_size == 0:
        print(f"  FAILED: {path} is missing or empty", file=sys.stderr)
        return False
    shapes = {model.COLLECTION: model for model in MODELS}
    counts: dict[str, int] = {}
    try:
        for name, doc in read(path):
            if name != COUNTERS:
                shapes[name].from_doc(doc)
            counts[name] = counts.get(name, 0) + 1
    except (OSError, EOFError, KeyError, TypeError, ValueError) as exc:
        print(f"  FAILED: {path} did not read back cleanly — {type(exc).__name__}: {exc}", file=sys.stderr)
        return False
    if not counts.get(User.COLLECTION):
        print(f"  FAILED: {path} holds no accounts", file=sys.stderr)
        return False
    print(f"  verified: {path.name}, {path.stat().st_size / 1024**2:.1f} MB, {sum(counts.values())} "
          f"documents, {counts[User.COLLECTION]} account(s)")
    return True


def prune(directory: Path, keep_days: int) -> int:
    """Delete backups older than the retention window, and the copies of the corpus archive made beside
    them; how many went. Each copy is the whole archive, so the newest holds everything the older ones do,
    and it is never one of those that go. None of them ever went: a whole copy a night, for good."""
    cutoff = datetime.now(UTC).timestamp() - keep_days * 86400
    old = [path for path in directory.glob("mahoraga-*.jsonl.gz") if path.stat().st_mtime < cutoff]
    old += [path for path in sorted(directory.glob("corpus-archive-*.tar.gz"))[:-1]
            if path.stat().st_mtime < cutoff]
    for path in old:
        path.unlink()
    return len(old)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="directory to write the backup into")
    parser.add_argument("--archive", help="corpus archive directory to copy alongside")
    parser.add_argument("--verify-only", help="verify an existing backup and exit")
    args = parser.parse_args(argv)

    if args.verify_only:
        return 0 if verify(Path(args.verify_only)) else 1

    # Read by the account that wrote it and nobody else: password hashes, every sealed credential and
    # people's own documents are in it, and it was written for anybody on the machine to read.
    os.umask(0o077)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    stamp = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    target = out / f"mahoraga-{stamp}.jsonl.gz"
    print(f"backing up to {target}")
    try:
        counts = write(database(), target)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    print(f"  wrote {counts}")
    if not verify(target):
        return 1

    # **The archive is the only copy of a posting once retention has deleted it**
    # (`libs.corpus.archive`). Copied whole rather than in parts: it is append-only by design, so each
    # copy holds all of it, and `prune` lets the old copies go but never the newest.
    if args.archive:
        source = Path(args.archive)
        if source.is_dir() and any(source.iterdir()):
            bundle = shutil.make_archive(str(out / f"corpus-archive-{stamp}"), "gztar", root_dir=source)
            print(f"  corpus archive: {Path(bundle).name}")
        else:
            # Not an error: nothing has aged out yet. Said so an empty archive is a fact somebody read.
            print(f"  corpus archive: nothing at {source} yet")

    # Not a flag: the Privacy Policy states this number (`libs.db.BACKUP_KEEP_DAYS`).
    removed = prune(out, BACKUP_KEEP_DAYS)
    if removed:
        print(f"  pruned {removed} backup(s) older than {BACKUP_KEEP_DAYS} days")

    print("\nREMINDER: SECRETS_KEK is NOT in this backup, on purpose. Back it up separately, to a "
          "different place, under different credentials — a restore without it yields a database "
          "of credentials nobody can decrypt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
