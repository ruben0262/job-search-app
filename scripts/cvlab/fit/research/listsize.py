"""How long a search's list is on the site, as `build.freeze` makes it, before anything is drawn: so a new
person's search words are chosen by the size of their list alone, never by a score.

    MONGODB_URI=mongodb://127.0.0.1:27017 MONGODB_DB=jobsearch_dev PYTHONPATH=. \
        python -m scripts.cvlab.fit.research.listsize '<json of name: spec>'
"""

from __future__ import annotations

import json
import sys

from libs.corpus.freshness import platform_windows
from libs.corpus.words import words_in
from libs.db import database
from libs.matching import green_words, has_any
from libs.tenant.filters import UserFilters, candidate_conditions
from scripts.cvlab.fit.build import config, group


def main(specs: dict) -> None:
    db = database()
    windows = platform_windows(db)
    for name, spec in specs.items():
        cfg = config(spec)
        words = green_words(cfg)
        filters = UserFilters.from_config(
            cfg, max_age_hours=windows.max_age_hours, source_min_ages=windows.source_min_ages
        )
        rows = list(
            db.corpus.find(
                {"$and": [*candidate_conditions(filters), has_any(words)]},
                {"title": 1, "company": 1, "description": 1},
            )
        )
        seen, titled, text = set(), 0, 0
        for row in rows:
            if (key := group(row)) not in seen:
                seen.add(key)
                if words_in(row["title"], words):
                    titled += 1
                else:
                    text += 1
        print(
            f"{name:20s} {json.dumps(spec['words'], ensure_ascii=False):70s} list {len(rows):5d} "
            f"once each {len(seen):5d} ({titled} titled, {text} text)"
        )


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
