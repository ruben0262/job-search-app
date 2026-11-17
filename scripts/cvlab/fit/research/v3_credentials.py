"""Version 3's credential fixes against version 2, on every labelled pair (owner, 2026-11-17: "lets do the
missing credential checks please see the effect").

Version 2 is `candidate.py`, the measured copy, equal to the shipped version 2 on all 922 pairs; version 3 is
the shipped `libs.fit`. Both use the weights fitted on the seen 27, which the fixes do not move: they change
only which pairs are capped, and the signals are asserted equal. The third held-out set is where the misses
were found, so its numbers are optimistic; a fourth held-out set reads version 3 fairly.

    PYTHONPATH=. python -m scripts.cvlab.fit.research.v3_credentials
"""
from __future__ import annotations

from libs import fit
from scripts.cvlab.fit.evaluate import SPLIT
from scripts.cvlab.fit.research import candidate as c
from scripts.cvlab.fit.research import harness as h
from scripts.cvlab.fit.research import variants as v

FRESH3 = list(SPLIT["fresh3"])
GROUPS = (("seen 27", h.SEEN), ("fresh 12", h.FRESH), ("fresh2 12", h.FRESH2),
          ("fresh3 12, where the misses were found", FRESH3))


def barred(made: dict, features: dict) -> bool:
    """`libs.fit.barred`, in Python."""
    has = set(made["has"])
    return (any(not set(group) & has for group in features["req"]) or features["staff"]
            or (features["student"] and not made["student"]))


def main() -> None:
    data = h.load()
    names = [name for _, group in GROUPS for name in group]
    _, _, x, caps2 = c.build(data, names)
    model = c.fitted(x, data.truth, [key for key in x if key[0] in h.SEEN])
    made3 = {name: fit.person(v.kinds_of(name), v.name_of(name)) for name in names}
    jobs = {key[1] for key in x}
    features3 = {job: fit.job(row["title"], row.get("description"), row.get("language"))
                 for job, row in data.postings.items() if job in jobs}
    assert h.py_x4(made3, features3, {key: data.truth[key] for key in x}) == x, "the signals are version 2's"
    caps3 = {key: barred(made3[key[0]], features3[key[1]]) for key in x}
    old = {key: model.score_capped(x[key], caps2[key]) for key in x}
    new = {key: model.score_capped(x[key], caps3[key]) for key in x}
    for title, group in GROUPS:
        h.show(f"version 3 against 2, {title}", new, old, data.truth, group)
    changed = sorted(key for key in x if caps2[key] != caps3[key])
    print(f"\npairs whose cap changed: {len(changed)} of {len(x)}")
    for key in changed:
        title = data.postings[key[1]]["title"][:48]
        shows = f"version 2 shows {fit.shown(old[key]):2d}, 3 shows {fit.shown(new[key]):2d}"
        print(f"  {key[0]:24s} {title:48s} label {data.truth[key]:4.1f}  {shows}  "
              f"{'capped' if caps3[key] else 'no longer capped'}")


if __name__ == "__main__":
    main()
