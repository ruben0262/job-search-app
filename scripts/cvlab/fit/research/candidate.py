"""The recommended combination, frozen before the held-out people were read: a person's roles read from the
role phrases of their CVs alone (`roles.role_phrases`, in place of a CV's first four lines, the CV reader's
job titles and a summary's first sentence), and a score capped at 4 when the posting requires a credential
they neither hold nor are working towards, or is open only to its own staff or to students (`requirements`).
Weights and by-rank steps are fitted as `model.py` fits them; the cap is the rubric's own rule ("a blocker
caps at 4").

As the site would run it: the posting's features gain `req` (groups of alternative credentials), `staff` and
`student`; the profile gains `has` (credentials held or half held) and `student`; `expression` is
`libs.fit.expression` with the cap applied to the calibrated number before the tie-breaking fraction.

    PYTHONPATH=. python -m scripts.cvlab.fit.research.candidate check   # database against Python, all pairs
    PYTHONPATH=. python -m scripts.cvlab.fit.research.candidate seen    # the 27 seen, one left out at a time
    PYTHONPATH=. python -m scripts.cvlab.fit.research.candidate fresh   # ONCE: fitted on the 27, read on 24
"""

from __future__ import annotations

import sys

from libs import fit
from scripts.cvlab.fit.research import harness as h
from scripts.cvlab.fit.research import requirements as rq
from scripts.cvlab.fit.research import variants as v

ROLES = {"phrases": True, "first_lines": False, "summaries": False, "held": False}
CAP = 4.0


def profile(name: str) -> dict:
    made = v.person(v.kinds_of(name), v.name_of(name), **ROLES)
    made.pop("years")
    held = rq.person(v.kinds_of(name))
    return {**made, "has": sorted(set(held["held"]) | set(held["half"])), "student": held["student"]}


def features(row: dict) -> dict:
    asked = rq.posting(row["title"], row.get("description"))
    return {
        **fit.job(row["title"], row.get("description"), row.get("language")),
        "req": asked["required"],
        "staff": asked["internal"],
        "student": asked["student"],
    }


def blocked(made: dict, feats: dict) -> bool:
    has = set(made["has"])
    return (
        any(not set(group) & has for group in feats["req"])
        or feats["staff"]
        or (feats["student"] and not made["student"])
    )


class Capped(h.Fitted):
    """`expression`'s number: the calibrated value, at most `CAP` for a blocked pair, plus the fraction."""

    def score_capped(self, xs: list[float], cap: bool) -> float:
        raw = h.raw_of(self.w, xs)
        calibrated = h.stepped(self.steps, raw)
        return (min(calibrated, CAP) if cap else calibrated) + 1e-3 * raw


def fitted(x: dict, truth: dict, keys: list) -> Capped:
    model = h.fit_linear(x, truth, keys)
    return Capped(model.w, model.steps)


def as_model(model: h.Fitted) -> dict:
    """The fitted model in `model.json`'s shape."""
    return {"weights": dict(zip((*fit.SIGNALS, "const"), model.w, strict=True)), "steps": model.steps}


def expression(made: dict, model: dict) -> dict:
    """`libs.fit.expression` with the cap, for the database."""
    weights, steps = model["weights"], model["steps"]
    raw = {
        "$add": [
            weights["const"],
            {"$multiply": [weights["title"], fit.gated("$$title", "$$cos")]},
            *({"$multiply": [weights[name], f"$${name}"]} for name in ("cos", "up", "blocked")),
        ]
    }
    calibrated = {
        "$switch": {
            "branches": [{"case": {"$lt": ["$$raw", upper]}, "then": value} for upper, value in steps[:-1]],
            "default": steps[-1][1],
        }
    }
    missing = {
        "$anyElementTrue": [
            {
                "$map": {
                    "input": {"$ifNull": ["$fit.req", []]},
                    "as": "group",
                    "in": {
                        "$eq": [{"$size": {"$setIntersection": ["$$group", {"$literal": made["has"]}]}}, 0]
                    },
                }
            }
        ]
    }
    capped = {
        "$or": [
            missing,
            {"$eq": ["$fit.staff", True]},
            {"$and": [{"$eq": ["$fit.student", True]}, {"$literal": not made["student"]}]},
        ]
    }
    return {
        "$cond": [
            {"$eq": [{"$ifNull": ["$fit", None]}, None]},
            None,
            {
                "$let": {
                    "vars": fit.signals(made),
                    "in": {
                        "$let": {
                            "vars": {"raw": raw},
                            "in": {
                                "$add": [
                                    {"$cond": [capped, {"$min": [calibrated, CAP]}, calibrated]},
                                    {"$multiply": [1e-3, "$$raw"]},
                                ]
                            },
                        }
                    },
                }
            },
        ]
    }


def build(data: h.Data, names: list[str]) -> tuple[dict, dict, dict, dict]:
    made = {name: profile(name) for name in names}
    feats = {job: features(row) for job, row in data.postings.items()}
    truth = {k: t for k, t in data.truth.items() if k[0] in names}
    x = h.py_x4(made, feats, truth)
    caps = {k: blocked(made[k[0]], feats[k[1]]) for k in truth}
    return made, feats, x, caps


def lopo(data: h.Data, names: list[str]) -> dict:
    _, _, x, caps = build(data, names)
    judged = {}
    for held in names:
        mine = [k for k in x if k[0] == held]
        classes = {data.copy[k[1]] for k in mine}
        train = [k for k in x if k[0] != held and data.copy[k[1]] not in classes]
        model = fitted(x, data.truth, train)
        judged.update({k: model.score_capped(x[k], caps[k]) for k in mine})
    return judged


def holdout(data: h.Data, train_names: list[str], judge_names: list[str]) -> tuple[dict, dict]:
    """The candidate and the current score, each fitted on `train_names` (less copies of the judged postings)
    and scored on `judge_names`."""
    names = [*train_names, *judge_names]
    _, _, x, caps = build(data, names)
    judged_keys = [k for k in x if k[0] in judge_names]
    classes = {data.copy[k[1]] for k in judged_keys}
    train = [k for k in x if k[0] in train_names and data.copy[k[1]] not in classes]
    model = fitted(x, data.truth, train)
    new = {k: model.score_capped(x[k], caps[k]) for k in judged_keys}
    old_x = h.py_x4(data.made, h.jobs_features(data.postings), {k: data.truth[k] for k in x})
    old_model = h.fit_linear(old_x, data.truth, train)
    old = {k: old_model.score(old_x[k]) for k in judged_keys}
    return new, old


def check(data: h.Data) -> None:
    """Every pair's candidate score by the database expression equals the Python one."""
    names = [*h.SEEN, *h.FRESH, *h.FRESH2]
    made, feats, x, caps = build(data, names)
    model = fitted(x, data.truth, [k for k in x if k[0] in h.SEEN])
    db = h.database()
    db.jobs.drop()
    db.jobs.insert_many([{"_id": job, "fit": feats[job]} for job in {k[1] for k in x}])
    worst, n = 0.0, 0
    for name in names:
        ids = [k[1] for k in x if k[0] == name]
        for row in db.jobs.aggregate(
            [{"$match": {"_id": {"$in": ids}}}, {"$project": {"s": expression(made[name], as_model(model))}}]
        ):
            worst = max(
                worst, abs(row["s"] - model.score_capped(x[name, row["_id"]], caps[name, row["_id"]]))
            )
            n += 1
    db.client.drop_database(h.SCRATCH)
    print(
        f"database against Python over {n} pairs: largest difference {worst:.2e}; "
        f"{sum(caps.values())} of {len(caps)} pairs capped"
    )


def main(command: str) -> None:
    data = h.load()
    if command == "check":
        check(data)
    elif command == "seen":
        base = h.baseline(data)
        judged = lopo(data, h.SEEN)
        h.show("candidate, 27 seen (LOPO)", judged, base, data.truth)
        h.absolute("current", base, data.truth)
        h.absolute("candidate", judged, data.truth)
    elif command == "fresh":
        for title, group in (
            ("fresh 12", h.FRESH),
            ("fresh2 12", h.FRESH2),
            ("all 24 held out", h.FRESH + h.FRESH2),
        ):
            new, old = holdout(data, h.SEEN, group)
            h.show(f"candidate vs current, {title}", new, old, data.truth, group)
            h.absolute(f"  current, {title}", old, data.truth, group)
            h.absolute(f"  candidate, {title}", new, data.truth, group)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "seen")
