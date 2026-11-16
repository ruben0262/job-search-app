"""Candidate 5, measured: a person's roles and a posting's title read by occupation words, against the current
readers, on the 27 people seen, left out one at a time, paired with the current score.

    PYTHONPATH=. python -m scripts.cvlab.fit.research.run_roles
"""

from __future__ import annotations

from scripts.cvlab.fit.research import harness as h
from scripts.cvlab.fit.research import variants as v

PERSON = {
    "current": {},
    "+phrases": {"phrases": True},
    "phrases+held, no first lines": {"phrases": True, "first_lines": False},
    "phrases+held, no first lines/summary": {"phrases": True, "first_lines": False, "summaries": False},
    "phrases only": {"phrases": True, "first_lines": False, "summaries": False, "held": False},
    "phrases (2 most recent) +held": {"phrases": True, "first_lines": False, "summaries": False, "recent": 2},
    "+phrases, compounds": {"phrases": True, "compounds": True},
    "phrases+held no first, compounds": {"phrases": True, "first_lines": False, "compounds": True},
}
JOB = {
    "current": {},
    "heads": {"core": "heads"},
    "heads+compounds": {"core": "heads", "compounds": True},
}


def run(names=h.SEEN, people=PERSON, jobs=JOB) -> dict:
    data = h.load()
    v.check(data.postings, names)
    base = h.baseline(data, names)
    h.absolute("current score", base, data.truth, names)
    results = {}
    features = {
        label: h.jobs_features(data.postings, lambda t, d, lang, s=switches: v.job(t, d, lang, **s))
        for label, switches in jobs.items()
    }
    for plabel, switches in people.items():
        made = v.profiles(names, **switches)
        for jlabel, feats in features.items():
            if plabel == "current" and jlabel == "current":
                continue
            x = h.py_x4(made, feats, {k: t for k, t in data.truth.items() if k[0] in names})
            judged = h.lopo(x, data, names)
            results[plabel, jlabel] = h.show(f"{plabel} | job {jlabel}"[:38], judged, base, data.truth, names)
    return results


if __name__ == "__main__":
    import sys

    if sys.argv[1:] == ["rest"]:
        run(
            people={key: PERSON[key] for key in ("+phrases, compounds", "phrases+held no first, compounds")},
            jobs=JOB,
        )
        run(
            people={
                "phrases only, compounds": {
                    "phrases": True,
                    "first_lines": False,
                    "summaries": False,
                    "held": False,
                    "compounds": True,
                },
                "phrases only, counted twice": {
                    "phrases": True,
                    "first_lines": False,
                    "summaries": False,
                    "held": False,
                    "phrase_count": 2,
                },
            },
            jobs={"current": {}, "current+compounds": {"compounds": True}},
        )
    else:
        run()
