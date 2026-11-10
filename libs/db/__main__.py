"""`python -m libs.db [--prune]`: apply the schema to `MONGODB_URI`/`MONGODB_DB` and say what is there.

What a deploy runs before it restarts anything, so a rule or index the database refuses stops the
deploy rather than a service at start-up. `--prune` also drops indexes this code no longer declares:
only the deploy passes it, from an image of the commit being deployed.

First the AI accounts linked before 2026-11-09 are settled (`libs.llm.accounts.settle`), which does
nothing once done: under the rules they were written with, since the new ones refuse any change to a
row of a kind they no longer allow, and the prune changes every row it removes a retired field from.
"""
from __future__ import annotations

import sys

from libs.db import database, ensure_schema
from libs.db.models import MODELS
from libs.llm.accounts import settle


def main() -> int:
    db = database()
    settle(db)
    ensure_schema(db, prune="--prune" in sys.argv[1:])
    for model in MODELS:
        indexes = len(list(db[model.COLLECTION].list_indexes()))
        print(f"{model.COLLECTION:22} {db[model.COLLECTION].estimated_document_count():>8} documents, "
              f"{indexes} index(es)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
