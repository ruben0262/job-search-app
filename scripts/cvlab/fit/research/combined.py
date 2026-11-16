"""The candidates that measured well, together, and what is left of the error: person by person and by label
band, on the 27 seen, left out one at a time, paired with the current score.

    PYTHONPATH=. python -m scripts.cvlab.fit.research.combined
"""

from __future__ import annotations

import statistics
from collections import defaultdict

from scripts.cvlab.fit.research import harness as h
from scripts.cvlab.fit.research import requirements as rq
from scripts.cvlab.fit.research import variants as v

ROLES = {"phrases": True, "first_lines": False, "summaries": False, "held": False}


def blockers(data: h.Data, names: list[str]) -> dict:
    jobs = {job: rq.posting(row["title"], row.get("description")) for job, row in data.postings.items()}
    people = {name: rq.person(v.kinds_of(name)) for name in names}
    return {k: rq.compare(jobs[k[1]], people[k[0]]) for k in data.truth if k[0] in names}


def capper(gaps: dict, block: float = 4.0, half: float | None = None):
    def post(key, score, xs):
        e = gaps[key]
        if e["missing"] or e["internal"] or e["student"]:
            return min(score, block + 1e-3 * score)
        if half is not None and e["half"]:
            return min(score, half + 1e-3 * score)
        return score

    return post


def x_for(data: h.Data, names: list[str], person_switches: dict) -> dict:
    made = v.profiles(names, **person_switches)
    feats = h.jobs_features(data.postings)
    return h.py_x4(made, feats, {k: t for k, t in data.truth.items() if k[0] in names})


def band_of(label: float) -> str:
    """A label's band, as the error analysis reads it."""
    for upper, band in ((2.75, "1-2"), (4.75, "3-4"), (6.75, "5-6"), (8.75, "7-8")):
        if label < upper:
            return band
    return "9-10"


def residuals(title: str, judged: dict, truth: dict) -> None:
    people = defaultdict(list)
    for k, score in judged.items():
        people[k[0]].append(round(score) - truth[k])
    print(
        f"  {title}: mean error by person "
        + ", ".join(
            f"{name} {statistics.fmean(errors):+.1f}"
            for name, errors in sorted(people.items(), key=lambda item: statistics.fmean(item[1]))
        )
    )
    bands = defaultdict(list)
    for k, score in judged.items():
        bands[band_of(truth[k])].append(round(score) - truth[k])
    print(
        f"  {title}: mean error by label band "
        + ", ".join(f"{band} {statistics.fmean(errors):+.2f}" for band, errors in sorted(bands.items()))
    )


def main(names=h.SEEN) -> None:
    data = h.load()
    base = h.baseline(data, names)
    gaps = blockers(data, names)
    x_roles = x_for(data, names, ROLES)
    roles = h.lopo(x_roles, data, names)
    h.show("roles", roles, base, data.truth, names)
    capped = h.lopo(data.x4, data, names, post=capper(gaps))
    h.show("blocker cap 4", capped, base, data.truth, names)
    both = h.lopo(x_roles, data, names, post=capper(gaps))
    h.show("roles + blocker cap 4", both, base, data.truth, names)
    both_half = h.lopo(x_roles, data, names, post=capper(gaps, half=6.0))
    h.show("roles + cap 4, half cap 6", both_half, base, data.truth, names)
    h.absolute("current", base, data.truth, names)
    h.absolute("roles + blocker cap 4", both, data.truth, names)
    residuals("current", base, data.truth)
    residuals("roles + cap", both, data.truth)


if __name__ == "__main__":
    main()
