"""What the profile reader makes of each seen person: their role words, their level, years, languages, the CV
reader's job titles, and the size of their word vector, beside the current score's mean error for them.

    PYTHONPATH=. python -m scripts.cvlab.fit.research.profiles
"""

from __future__ import annotations

import json
import re
import statistics

from libs.corpus.langfilter import detect_language
from libs.documents.read import read
from scripts.cvlab.fit.build import PANEL, documents
from scripts.cvlab.fit.research import harness as h


def kinds_of(name: str) -> dict[str, list[str]]:
    kinds: dict[str, list[str]] = {}
    for filename, text in documents(name):
        kinds.setdefault(re.sub(r"-\d+$", "", filename.rsplit(".", 1)[0]), []).append(text)
    return kinds


def main() -> None:
    data = h.load()
    judged = h.baseline(data)
    for name in h.SEEN:
        me = data.made[name]
        kinds = kinds_of(name)
        facts = read(kinds)
        err = statistics.fmean(round(judged[k]) - data.truth[k] for k in judged if k[0] == name)
        roles = sorted(word for word, value in me["title"].items() if value == 1.0)
        cv_lang = detect_language(" ".join(kinds.get("cv", [""])))
        candidate = json.loads((PANEL / name / "candidate.json").read_text(encoding="utf-8"))
        print(
            f"{name:17s} err {err:+.2f} lang {cv_lang} words {len(me['vector']):4d} level {me['level']} "
            f"years {facts.years} spoken {me['spoken']} declared {candidate.get('languages')}"
        )
        print(f"    roles: {roles}")
        print(f"    titles: {[job.title for job in facts.jobs][:6]}")


if __name__ == "__main__":
    main()
