"""Fit the fit score's weights and its steps to 1-10 (`libs/fit/model.json`) to the labels, and judge them.

    python -m scripts.cvlab.fit.model check    # leave one person out, inside the dev half: for choosing
    python -m scripts.cvlab.fit.model test     # fitted on dev, judged on the test half: read once, at the end
    python -m scripts.cvlab.fit.model write    # fitted on everybody, written to libs/fit/model.json

**What the site runs is what is measured.** The frozen postings get their features from `libs.fit.job` and the
people their profiles from `libs.fit.person`, in a scratch database on the local mongod (`MONGODB_URI`, never
Atlas); the four signals are read with the site's own expressions (`libs.fit.signals`), and the judged scores
with the whole of `libs.fit.expression`. Only the fitting itself is Python: least squares for the weights (a
small penalty, the intercept free), then the steps from their sum to 1-10 by isotonic regression, so a higher
sum never shows a lower number.
"""
from __future__ import annotations

import json
import os
import re
import sys

from pymongo import MongoClient

from libs import fit
from scripts.cvlab.fit.build import FIT, PANEL, documents, read_jsonl
from scripts.cvlab.fit.evaluate import SPLIT, by_sector, labels, measure, report

SCRATCH = "fit_model_scratch"
# How much the weights are pulled towards nothing, against the size of the evaluation set.
PENALTY = 1.0


def _db():
    uri = os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27017")
    if "mongodb.net" in uri:
        raise SystemExit("the fit model is fitted on a local mongod, never Atlas")
    return MongoClient(uri, tz_aware=True)[SCRATCH]


def profiles() -> dict[str, dict]:
    made = {}
    for folder in sorted(PANEL.iterdir()):
        if (folder / "candidate.json").exists():
            kinds: dict[str, list[str]] = {}
            for filename, text in documents(folder.name):
                kinds.setdefault(re.sub(r"-\d+$", "", filename.rsplit(".", 1)[0]), []).append(text)
            name = json.loads((folder / "candidate.json").read_text(encoding="utf-8")).get("name")
            made[folder.name] = fit.person(kinds, name)
    return made


def load(db) -> None:
    """The frozen postings, each with the features the site would store on it."""
    db.jobs.drop()
    db.jobs.insert_many([{"_id": row["id"],
                          "fit": fit.job(row["title"], row.get("description"), row.get("language"))}
                         for row in read_jsonl(FIT / "postings.jsonl")])


def read_signals(db, made: dict[str, dict], truth: dict) -> dict[tuple[str, int], list[float]]:
    """Each pair's four signals, the title gated as `expression` gates it, worked out by the database."""
    out = {}
    for name, me in made.items():
        ids = [job for person, job in truth if person == name]
        rows = db.jobs.aggregate([{"$match": {"_id": {"$in": ids}}}, {"$project": {"s": {"$let": {
            "vars": fit.signals(me), "in": [fit.gated("$$title", "$$cos"), "$$cos", "$$up", "$$blocked"]}}}}])
        out.update({(name, row["_id"]): row["s"] for row in rows})
    return out


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    m = [[*row, value] for row, value in zip(a, b, strict=True)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(n):
            if r != col and m[col][col]:
                factor = m[r][col] / m[col][col]
                m[r] = [x - factor * y for x, y in zip(m[r], m[col], strict=True)]
    return [m[i][n] / m[i][i] for i in range(n)]


def fitted(x: dict, truth: dict, keys: list) -> dict:
    """Weights by least squares, then steps by isotonic regression of the labels on the weighted sum."""
    rows = [[*x[key], 1.0] for key in keys]
    k = len(rows[0])
    a = [[sum(r[i] * r[j] for r in rows) + (PENALTY if i == j and i < k - 1 else 0) for j in range(k)]
         for i in range(k)]
    w = _solve(a, [sum(r[i] * truth[key] for r, key in zip(rows, keys, strict=True)) for i in range(k)])
    weights = {**dict(zip(fit.SIGNALS, (round(v, 4) for v in w), strict=False)), "const": round(w[-1], 4)}
    raws = sorted(sum(weights[n] * v for n, v in zip(fit.SIGNALS, x[key], strict=True)) + weights["const"]
                  for key in keys)
    # By rank: the sum at a posting's place among the readings shows the label the readers gave that place, so
    # the numbers spread over the scale as the readers' do. The mean label at each sum (isotonic) showed a 9
    # or 10 as 6 or 7 and a 1 or 2 as 3 or 4; by rank it is 1.9 and 1.1 off, a 7+ given to 66% of the jobs
    # readers put there against 38%, the order the same and the mean error 1.76 against 1.68 (2026-11-16).
    # Each step begins at a sum a reading sits on, kept whole: rounded to four places it fell just above that
    # reading's own sum, which took the step below, 10 of 922 pairs (2026-11-16, night).
    steps: list[list] = []
    for raw, label in zip(raws, sorted(truth[key] for key in keys), strict=True):
        if not steps or label > steps[-1][1]:
            steps.append([raw, label])
    return {"weights": weights,
            "steps": [[steps[i + 1][0], steps[i][1]] for i in range(len(steps) - 1)] + [[None, steps[-1][1]]]}


def scores(db, made: dict, truth: dict, model: dict, names: list[str]) -> dict:
    """The site's whole expression, with `model`, for these people's pairs."""
    out = {}
    for name in names:
        ids = [job for person, job in truth if person == name]
        for row in db.jobs.aggregate([{"$match": {"_id": {"$in": ids}}},
                                      {"$project": {"fit": fit.expression(made[name], model)}}]):
            out[name, row["_id"]] = row["fit"]
    return out


def main(command: str) -> None:
    db, truth, made = _db(), labels(), profiles()
    load(db)
    x = read_signals(db, made, truth)
    dev, test, fresh = list(SPLIT["dev"]), list(SPLIT["test"]), list(SPLIT["fresh"])
    # The first two halves were both looked at while the score was built: together, what it is built on.
    seen = dev + test
    if command == "check":
        judged = {}
        for held in seen:
            model = fitted(x, truth, [key for key in x if key[0] in seen and key[0] != held])
            judged |= scores(db, made, truth, model, [held])
        result = measure(judged, truth, seen, scale=True)
        report("leave one person out, seen", result)
        by_sector(result)
    elif command == "test":
        model = fitted(x, truth, [key for key in x if key[0] in dev])
        result = measure(scores(db, made, truth, model, test), truth, test, scale=True)
        report("fitted on dev, judged on test", result)
        by_sector(result)
    elif command == "fresh":
        # Fitted on the people seen, judged on the ones drawn after: the held-out number.
        model = fitted(x, truth, [key for key in x if key[0] in seen])
        result = measure(scores(db, made, truth, model, fresh), truth, fresh, scale=True)
        report("fitted on seen, judged on fresh", result)
        by_sector(result)
        from scripts.cvlab.fit.baselines import words_found  # noqa: PLC0415
        from scripts.cvlab.fit.build import people  # noqa: PLC0415
        from scripts.cvlab.fit.evaluate import paired  # noqa: PLC0415
        postings = {row["id"]: row for row in read_jsonl(FIT / "postings.jsonl")}
        searches = people()
        today = {key: words_found(key[0], [], searches[key[0]], postings[key[1]]) for key in truth
                 if key[0] in fresh}
        baseline = measure(today, truth, fresh, scale=False)
        report("today's order (words found), fresh", baseline)
        paired(result, baseline, fresh, "fit minus today, fresh")
    else:
        # Fitted on the people seen; the fresh 12 stay held out for the next model to be judged on.
        x = {key: value for key, value in x.items() if key[0] in seen}
        model = fitted(x, truth, list(x))
        result = measure(scores(db, made, truth, model, seen), truth, seen, scale=True)
        report("fitted on seen, judged on seen (optimistic)", result)
        path = fit._HERE / "model.json"
        path.write_text(json.dumps({**model, "version": fit.VERSION, "pairs": len(x)}, indent=1) + "\n",
                        encoding="utf-8")
        print(f"written {path}")
    db.client.drop_database(SCRATCH)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "check")
