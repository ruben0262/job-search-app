"""Candidate 8 measured: cleaner text (`cleaning.patched`), each part alone and together, with the current
readers and with the role phrases, on the 27 seen, left out one at a time, paired with the current score.

    PYTHONPATH=. python -m scripts.cvlab.fit.research.run_cleaning
"""

from __future__ import annotations

from scripts.cvlab.fit.research import harness as h
from scripts.cvlab.fit.research import variants as v
from scripts.cvlab.fit.research.cleaning import patched
from scripts.cvlab.fit.research.combined import ROLES


def x_with(data: h.Data, names: list[str], switches: dict, **cleaning) -> dict:
    with patched(**cleaning):
        made = v.profiles(names, **switches)
        feats = h.jobs_features(data.postings, v.job)
        return h.py_x4(made, feats, {k: t for k, t in data.truth.items() if k[0] in names})


def main(names=h.SEEN) -> None:
    data = h.load()
    base = h.baseline(data, names)
    # The patch switched off gives the shipped signals.
    same = x_with(data, names, {}, stop=False, personal=False, offer=False)
    assert max(abs(a - b) for k in same for a, b in zip(same[k], data.x4[k], strict=True)) < 1e-9
    for label, cleaning in (
        ("function words", {"personal": False, "offer": False}),
        ("personal data lines", {"stop": False, "offer": False}),
        ("package headings", {"stop": False, "personal": False}),
        ("all cleaning", {}),
    ):
        x = x_with(data, names, {}, **cleaning)
        h.show(f"clean: {label}", h.lopo(x, data, names), base, data.truth, names)
    x = x_with(data, names, ROLES)
    h.show("clean: all + roles", h.lopo(x, data, names), base, data.truth, names)


if __name__ == "__main__":
    main()
