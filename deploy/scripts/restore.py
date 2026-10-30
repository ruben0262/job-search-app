"""Put a backup back, into an empty database — or, with `--check`, prove that it would go.

`--check` reads every document into its collection's class without writing anything: the nightly
proof that a backup is a backup. A real restore applies the schema first, then the documents, then
moves each integer counter past the highest id restored, so a new job or notification cannot reuse one.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backup import read, verify

from libs.db import database, ensure_schema
from libs.db.models import MODELS, User

BATCH = 500


def restore(db, path: Path) -> dict[str, int]:
    """Write every document in the backup. Refuses a database that already holds accounts."""
    if db[User.COLLECTION].estimated_document_count():
        raise SystemExit(f"REFUSING: {db.name} already holds accounts — restore into an empty database")
    ensure_schema(db)
    counts: dict[str, int] = {}
    pending: dict[str, list[dict]] = {}
    for name, doc in read(path):
        pending.setdefault(name, []).append(doc)
        if len(pending[name]) >= BATCH:
            counts[name] = counts.get(name, 0) + len(db[name].insert_many(pending.pop(name)).inserted_ids)
    for name, docs in pending.items():
        counts[name] = counts.get(name, 0) + len(db[name].insert_many(docs).inserted_ids)
    for model in MODELS:
        if model.SEQUENCE:
            highest = db[model.COLLECTION].find_one({}, {"_id": 1}, sort=[("_id", -1)])
            if highest is not None:
                db.counters.update_one({"_id": model.COLLECTION}, {"$max": {"value": highest["_id"]}},
                                       upsert=True)
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="the backup file")
    parser.add_argument("--check", action="store_true", help="read it back, write nothing")
    parser.add_argument("--into", help="the database to restore into (MONGODB_DB otherwise)")
    args = parser.parse_args(argv)

    if not verify(Path(args.path)):
        return 1
    if args.check:
        return 0
    if args.into:
        os.environ["MONGODB_DB"] = args.into
    print(f"restored {restore(database(), Path(args.path))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
