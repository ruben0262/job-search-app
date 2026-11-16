"""Candidates 1-3 and eligibility, measured: each requirement gap added to the current four signals, as a
weight fitted with them or as a cap after the fit, on the 27 seen, left out one at a time, paired with the
current score.

    PYTHONPATH=. python -m scripts.cvlab.fit.research.run_requirements
"""

from __future__ import annotations

from scripts.cvlab.fit.research import harness as h
from scripts.cvlab.fit.research import requirements as rq
from scripts.cvlab.fit.research import variants as v


def gaps(data: h.Data, names: list[str]) -> dict:
    """Each pair's requirement gaps, and the years asked against the CV reader's years."""
    jobs = {job: rq.posting(row["title"], row.get("description")) for job, row in data.postings.items()}
    people = {name: rq.person(v.kinds_of(name)) for name in names}
    years = {name: v.person(v.kinds_of(name), v.name_of(name))["years"] for name in names}
    out = {}
    for key in data.truth:
        if key[0] not in names:
            continue
        gap = rq.compare(jobs[key[1]], people[key[0]])
        asked, have = jobs[key[1]]["years"], years[key[0]]
        gap["years_gap"] = min(5, max(0, asked - have)) if asked and have is not None else 0
        gap["years_short"] = int(bool(asked and have is not None and asked > have))
        gap["years_asked"] = min(10, asked or 0)
        out[key] = gap
    return out


def main(names=h.SEEN) -> None:
    data = h.load()
    base = h.baseline(data, names)
    g = gaps(data, names)
    x4 = data.x4

    def run(title, extra, post=None):
        x = {k: [*x4[k], *extra(g[k])] for k in g}
        return h.show(title, h.lopo(x, data, names, post=post), base, data.truth, names)

    run("+ missing credential (count)", lambda e: [min(2, e["missing"])])
    run("+ missing credential (0/1)", lambda e: [float(e["missing"] > 0)])
    run("+ missing, + half", lambda e: [float(e["missing"] > 0), float(e["half"] > 0)])
    run("+ degree short", lambda e: [e["degree_gap"]])
    run("+ internal only", lambda e: [e["internal"]])
    run("+ student only", lambda e: [e["student"]])
    run("+ years short (gap)", lambda e: [e["years_gap"]])
    run("+ years short (0/1)", lambda e: [e["years_short"]])
    run("+ years asked", lambda e: [e["years_asked"]])
    run(
        "+ all requirement gaps",
        lambda e: [
            float(e["missing"] > 0),
            float(e["half"] > 0),
            e["degree_gap"],
            e["internal"],
            e["student"],
            e["years_gap"],
        ],
    )
    run(
        "+ blocker (missing|internal|student)",
        lambda e: [float(e["missing"] > 0 or e["internal"] or e["student"])],
    )
    for cap in (4.0, 5.0, 6.0):

        def capped(key, score, xs, c=cap):
            e = g[key]
            return min(score, c + 1e-3 * score) if (e["missing"] or e["internal"] or e["student"]) else score

        run(f"cap {cap} on a blocker", lambda e: [], post=capped)
    for cap in (5.0, 6.0):

        def capped_half(key, score, xs, c=cap):
            e = g[key]
            if e["missing"] or e["internal"] or e["student"]:
                return min(score, 4.0 + 1e-3 * score)
            return min(score, c + 1e-3 * score) if e["half"] else score

        run(f"cap 4 blocker, {cap} half", lambda e: [], post=capped_half)


if __name__ == "__main__":
    main()
