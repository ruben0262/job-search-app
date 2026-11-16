"""`python -m libs.fit common`: count, from the corpus of `MONGODB_DB`, how many postings in each language
have each word, and write `common.json` with the words under `CAP` only (a rarer word weighs `CAP`). Read
the way `job` reads a posting, so a word is counted as it is weighed. Written again only on purpose, with
`VERSION` moved: every score depends on it."""
from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict

from libs.db import database
from libs.db.models import CorpusJob
from libs.fit import _HERE, _OWN_COUNT, CAP
from libs.fit.text import words, working_text


def common() -> None:
    counts: Counter = Counter()
    found: dict[str, Counter] = defaultdict(Counter)
    for row in database()[CorpusJob.COLLECTION].find({"described_at": {"$ne": None}},
                                                     {"title": 1, "description": 1, "language": 1}):
        seen = set(words(row["title"])) | set(words(working_text(row.get("description"))))
        for table in {row.get("language") or "*", "*"}:
            counts[table] += 1
            found[table].update(seen)
    kept = {table: n for table, n in counts.items() if table == "*" or n >= _OWN_COUNT}
    tables = {table: {word: df for word, df in sorted(found[table].items())
                      if math.log((kept[table] + 1) / (df + 1)) < CAP} for table in sorted(kept)}
    written = json.dumps({"n": dict(sorted(kept.items())), "df": tables}, ensure_ascii=False,
                         separators=(",", ":"))
    (_HERE / "common.json").write_text(written + "\n", encoding="utf-8")
    print(f"common.json: {sum(map(len, tables.values()))} words in {len(tables)} tables, "
          f"from {counts['*']} postings")


if __name__ == "__main__":
    {"common": common}[sys.argv[1]]()
