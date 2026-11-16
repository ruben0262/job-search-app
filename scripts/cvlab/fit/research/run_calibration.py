"""Candidate 7: the per-person bias and the steps. Features that move a whole list (the language a person and
a posting are in, how much a person wrote, whether the role reader found their roles), and the mean label at
each sum (isotonic) in place of the by-rank steps. On the 27 seen, left out one at a time, paired with the
current score. The weights fitted to order each person's list instead of every label pooled are `pairwise.py`.

    PYTHONPATH=. python -m scripts.cvlab.fit.research.run_calibration
"""

from __future__ import annotations

import math

from libs.corpus.langfilter import detect_language
from scripts.cvlab.fit.research import harness as h
from scripts.cvlab.fit.research import variants as v
from scripts.cvlab.fit.research.roles import role_phrases


def fit_isotonic(x: dict, truth: dict, keys: list) -> h.Fitted:
    """Least squares, then the mean label at each sum (pool adjacent violators), the earlier mapping."""
    base = h.fit_linear(x, truth, keys)
    pairs = sorted((h.raw_of(base.w, x[k]), truth[k]) for k in keys)
    blocks = []
    for raw, label in pairs:
        blocks.append([raw, label, 1])
        while len(blocks) > 1 and blocks[-2][1] / blocks[-2][2] >= blocks[-1][1] / blocks[-1][2]:
            low, total, count = blocks[-2]
            blocks[-2:] = [[low, total + blocks[-1][1], count + blocks[-1][2]]]
    steps = [[blocks[i + 1][0], round(blocks[i][1] / blocks[i][2], 4)] for i in range(len(blocks) - 1)]
    return h.Fitted(base.w, [*steps, [None, round(blocks[-1][1] / blocks[-1][2], 4)]])


def main(names=h.SEEN) -> None:
    data = h.load()
    base = h.baseline(data, names)
    x4 = {k: v_ for k, v_ in data.x4.items() if k[0] in names}
    h.show("isotonic steps", h.lopo(x4, data, names, fitter=fit_isotonic), base, data.truth, names)
    # Whole-list features: what a person wrote and in which language, and the posting's language.
    cv_en = {
        name: float((detect_language(" ".join(v.kinds_of(name).get("cv", [""]))) or "en") == "en")
        for name in names
    }
    size = {name: math.log(len(data.made[name]["vector"])) for name in names}
    found = {
        name: float(any(role_phrases(text, v.name_of(name)) for text in v.kinds_of(name).get("cv", [])))
        for name in names
    }
    job_en = {job: float((row.get("language") or "en") == "en") for job, row in data.postings.items()}
    extras = {
        "+ cv in English": lambda k: [cv_en[k[0]]],
        "+ posting in English": lambda k: [job_en[k[1]]],
        "+ cos x posting in English": lambda k: [job_en[k[1]], x4[k][1] * job_en[k[1]]],
        "+ log words they wrote": lambda k: [size[k[0]]],
        "+ roles found": lambda k: [found[k[0]]],
        "+ cv English, posting English, words": lambda k: [cv_en[k[0]], job_en[k[1]], size[k[0]]],
    }
    for title, extra in extras.items():
        x = {k: [*x4[k], *extra(k)] for k in x4}
        h.show(title, h.lopo(x, data, names), base, data.truth, names)


if __name__ == "__main__":
    main()
