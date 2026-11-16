"""What every experiment in this folder is measured with: the current score's four signals, worked out by the
database as the site works them out; a fit of weights and steps to 1-10 exactly as `model.py` fits them; leave
one person out over the 27 people seen; and a paired comparison against the current score, person by person,
with intervals from resampling people. Nothing here runs on the site.

**Copies stay on one side.** A posting can be in two people's lists (28 are), and one role listed in several
cities is several ids with one text (`copy`). When a person is held out, every pair whose posting is a copy of
one of theirs leaves the training set with them, so no weight is fitted on a posting it is then judged on.

**The measures**, as `evaluate.measure` takes them: per person, rank correlation (`rho`), clear pairs in order
(`pairs`), the top five's mean label (`top5`); over pairs, the shown number's error (`mae`) and a "7 or more"
filter's precision and recall (`p7`, `r7`). `paired` resamples people for every one of them, so a pooled
measure's interval is a people's interval too."""

from __future__ import annotations

import hashlib
import math
import os
import random
import statistics
from collections import defaultdict
from dataclasses import dataclass, field

from pymongo import MongoClient

from libs import fit
from scripts.cvlab.fit import model as fitmodel
from scripts.cvlab.fit.build import FIT, read_jsonl
from scripts.cvlab.fit.evaluate import SECTOR, SPLIT, labels, pair_order, spearman

SCRATCH = "fit_research_scratch"
SEEN = [*SPLIT["dev"], *SPLIT["test"]]
FRESH = list(SPLIT["fresh"])
PENALTY = fitmodel.PENALTY


def database():
    uri = os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27017")
    if "mongodb.net" in uri:
        raise SystemExit("research runs on a local mongod, never Atlas")
    return MongoClient(uri, tz_aware=True)[SCRATCH]


@dataclass
class Data:
    truth: dict  # (person, job) -> label
    rows: dict  # (person, job) -> the label row (readings, blockers, reasons)
    postings: dict  # job -> posting
    made: dict  # person -> libs.fit.person profile
    x4: dict  # (person, job) -> [gated title, cos, up, blocked], by the database
    copy: dict = field(default_factory=dict)  # job -> the id of its copy class


def _copies(postings: dict) -> dict:
    """Each posting's class: postings with one text, or one company and title, are one class."""
    parent = {job: job for job in postings}

    def root(job):
        while parent[job] != job:
            parent[job] = parent[parent[job]]
            job = parent[job]
        return job

    seen: dict[str, int] = {}
    for job, row in postings.items():
        text = (row.get("description") or "").strip()
        same_text = [f"t:{hashlib.sha256(text.encode()).hexdigest()}"] if len(text) > 200 else []
        for key in [*same_text, f"g:{row.get('group')}"]:
            if key in seen:
                parent[root(job)] = root(seen[key])
            else:
                seen[key] = job
    return {job: root(job) for job in postings}


def load(keep_db: bool = False) -> Data:
    db = database()
    truth = labels()
    rows = {(row["person"], row["job"]): row for row in read_jsonl(FIT / "labels.jsonl")}
    postings = {row["id"]: row for row in read_jsonl(FIT / "postings.jsonl")}
    made = fitmodel.profiles()
    fitmodel.load(db)
    x4 = fitmodel.read_signals(db, made, truth)
    if not keep_db:
        db.client.drop_database(SCRATCH)
    return Data(truth, rows, postings, made, x4, _copies(postings))


# ── fitting, as model.py fits ─────────────────────────────────────────────────────────────────────


def ridge(x: dict, truth: dict, keys: list, penalty: float = PENALTY) -> list[float]:
    """Least squares, a small penalty on every weight but the intercept (the last)."""
    rows = [[*x[key], 1.0] for key in keys]
    k = len(rows[0])
    a = [
        [sum(r[i] * r[j] for r in rows) + (penalty if i == j and i < k - 1 else 0) for j in range(k)]
        for i in range(k)
    ]
    return fitmodel._solve(
        a, [sum(r[i] * truth[key] for r, key in zip(rows, keys, strict=True)) for i in range(k)]
    )


def raw_of(w: list[float], xs: list[float]) -> float:
    return sum(a * b for a, b in zip(w, [*xs, 1.0], strict=True))


def by_rank(raws: list[float], labels_: list[float]) -> list[list]:
    """`model.fitted`'s steps: the sum at a place among the readings shows the label readers gave there."""
    steps: list[list] = []
    for raw, label in zip(sorted(raws), sorted(labels_), strict=True):
        if not steps or label > steps[-1][1]:
            steps.append([raw, label])
    return [[steps[i + 1][0], steps[i][1]] for i in range(len(steps) - 1)] + [[None, steps[-1][1]]]


def stepped(steps: list[list], raw: float) -> float:
    """`expression`'s `$switch`: the first step whose upper bound the sum is under, else the last."""
    for upper, value in steps[:-1]:
        if raw < upper:
            return value
    return steps[-1][1]


@dataclass
class Fitted:
    w: list[float]
    steps: list[list]

    def score(self, xs: list[float]) -> float:
        raw = raw_of(self.w, xs)
        return stepped(self.steps, raw) + 1e-3 * raw


def fit_linear(x: dict, truth: dict, keys: list, penalty: float = PENALTY) -> Fitted:
    w = ridge(x, truth, keys, penalty)
    w = [round(v, 4) for v in w]
    return Fitted(w, by_rank([raw_of(w, x[key]) for key in keys], [truth[key] for key in keys]))


def lopo(x: dict, data: Data, names: list[str] = SEEN, fitter=fit_linear, post=None) -> dict:
    """Leave one person out over `names`: fitted on the others (less every copy of the held-out person's
    postings), scored on them. `post(key, score, xs)` changes a score after the fit, for a rule not fitted."""
    judged = {}
    keys_all = [key for key in x if key[0] in names]
    for held in names:
        mine = [key for key in keys_all if key[0] == held]
        classes = {data.copy[key[1]] for key in mine}
        train = [key for key in keys_all if key[0] != held and data.copy[key[1]] not in classes]
        model = fitter(x, data.truth, train)
        for key in mine:
            value = model.score(x[key])
            judged[key] = post(key, value, x[key]) if post else value
    return judged


def holdout(
    x: dict, data: Data, train_names: list[str], judge_names: list[str], *, fitter=fit_linear, post=None
) -> dict:
    """Fitted on `train_names` (less copies of the judged people's postings), scored on `judge_names`."""
    judged_keys = [key for key in x if key[0] in judge_names]
    classes = {data.copy[key[1]] for key in judged_keys}
    train = [key for key in x if key[0] in train_names and data.copy[key[1]] not in classes]
    model = fitter(x, data.truth, train)
    out = {}
    for key in judged_keys:
        value = model.score(x[key])
        out[key] = post(key, value, x[key]) if post else value
    return out


# ── measures ───────────────────────────────────────────────────────────────────────────────────────


def per_person(scored: dict, truth: dict, names: list[str]) -> dict:
    """Every measure's parts for each person: the per-person ones, and the counts pooled ones are made of."""
    out = {}
    for name in names:
        keys = sorted(key for key in scored if key[0] == name)
        if not keys:
            continue
        s, t = [scored[key] for key in keys], [truth[key] for key in keys]
        order, n = pair_order(s, t)
        mine = [label for _, label in sorted(zip(s, t, strict=True), key=lambda each: -each[0])[:5]]
        shown = [round(value) for value in s]
        errors = [a - b for a, b in zip(shown, t, strict=True)]
        out[name] = {
            "rho": spearman(s, t),
            "pairs": order if n else None,
            "top5": statistics.fmean(mine),
            "abs": sum(abs(e) for e in errors),
            "sq": sum(e * e for e in errors),
            "n": len(errors),
            "bias": sum(errors),
            "h7": sum(a >= 7 and b >= 7 for a, b in zip(shown, t, strict=True)),
            "c7": sum(a >= 7 for a in shown),
            "a7": sum(b >= 7 for b in t),
            "h3": sum(a <= 3 and b <= 3 for a, b in zip(shown, t, strict=True)),
            "c3": sum(a <= 3 for a in shown),
            "a3": sum(b <= 3 for b in t),
        }
    return out


def _ratio(a: float, b: float) -> float:
    return a / b if b else float("nan")


def summary(per: dict, names: list[str]) -> dict:
    rows = [per[name] for name in names if name in per]
    total = {
        field_: sum(row[field_] for row in rows)
        for field_ in ("abs", "sq", "n", "bias", "h7", "c7", "a7", "h3", "c3", "a3")
    }
    return {
        "rho": statistics.fmean(row["rho"] for row in rows if row["rho"] is not None),
        "pairs": statistics.fmean(row["pairs"] for row in rows if row["pairs"] is not None),
        "top5": statistics.fmean(row["top5"] for row in rows),
        "mae": total["abs"] / total["n"],
        "rmse": (total["sq"] / total["n"]) ** 0.5,
        "bias": total["bias"] / total["n"],
        "p7": _ratio(total["h7"], total["c7"]),
        "r7": _ratio(total["h7"], total["a7"]),
        "p3": _ratio(total["h3"], total["c3"]),
        "r3": _ratio(total["h3"], total["a3"]),
    }


MEASURES = ("rho", "pairs", "top5", "mae", "p7", "r7")


def paired(new: dict, old: dict, truth: dict, names: list[str], *, rounds: int = 2000,
           seed: int = 11) -> dict:
    """`new` minus `old` on every measure: the difference over `names`, a 95% interval from resampling people
    (the same draw for both), and for the per-person measures how many people were better and worse."""
    pn, po = per_person(new, truth, names), per_person(old, truth, names)
    names = [name for name in names if name in pn and name in po]
    base_new, base_old = summary(pn, names), summary(po, names)
    draw = random.Random(seed)  # noqa: S311 - a seeded resample, not a secret
    samples = defaultdict(list)
    for _ in range(rounds):
        pick = draw.choices(names, k=len(names))
        sn, so = summary_of(pn, pick), summary_of(po, pick)
        for measure in MEASURES:
            samples[measure].append(sn[measure] - so[measure])
    out = {}
    for measure in MEASURES:
        values = sorted(v for v in samples[measure] if not math.isnan(v))
        low, high = values[int(len(values) * 0.025)], values[int(len(values) * 0.975) - 1]
        entry = {
            "new": base_new[measure],
            "old": base_old[measure],
            "delta": base_new[measure] - base_old[measure],
            "low": low,
            "high": high,
        }
        if measure in ("rho", "pairs", "top5"):
            diffs = [
                pn[n][measure] - po[n][measure]
                for n in names
                if pn[n][measure] is not None and po[n][measure] is not None
            ]
            entry["better"] = sum(d > 1e-9 for d in diffs)
            entry["worse"] = sum(d < -1e-9 for d in diffs)
        out[measure] = entry
    return out


def summary_of(per: dict, pick: list[str]) -> dict:
    """`summary` over a resample of people, a person drawn twice counting twice."""
    rows = [per[name] for name in pick]
    total = {field_: sum(row[field_] for row in rows) for field_ in ("abs", "n", "h7", "c7", "a7")}
    rhos = [row["rho"] for row in rows if row["rho"] is not None]
    pairs = [row["pairs"] for row in rows if row["pairs"] is not None]
    return {
        "rho": statistics.fmean(rhos),
        "pairs": statistics.fmean(pairs),
        "top5": statistics.fmean(row["top5"] for row in rows),
        "mae": total["abs"] / total["n"],
        "p7": _ratio(total["h7"], total["c7"]),
        "r7": _ratio(total["h7"], total["a7"]),
    }


def line(title: str, result: dict) -> str:
    """One row: each measure's new value and its paired difference with the interval."""
    parts = []
    for measure in MEASURES:
        each = result[measure]
        better = f" {each['better']}/{each['worse']}" if "better" in each else ""
        parts.append(
            f"{measure} {each['new']:.3f} {each['delta']:+.3f} [{each['low']:+.3f},{each['high']:+.3f}]"
            f"{better}"
        )
    return f"{title:38s} " + "  ".join(parts)


def show(title: str, new: dict, old: dict, truth: dict, names: list[str] = SEEN) -> dict:
    result = paired(new, old, truth, names)
    print(line(title, result), flush=True)
    return result


def absolute(title: str, scored: dict, truth: dict, names: list[str] = SEEN) -> dict:
    result = summary(per_person(scored, truth, names), names)
    print(f"{title:38s} " + "  ".join(f"{m} {result[m]:.3f}" for m in (*MEASURES, "bias", "p3", "r3")))
    return result


def baseline(data: Data, names: list[str] = SEEN) -> dict:
    """The current score, left out one person at a time: the reference every candidate is paired with."""
    return lopo(data.x4, data, names)


def sector_of(name: str) -> str:
    return SECTOR[name]


FRESH2 = list(SPLIT["fresh2"])


# ── the four signals in Python, as `libs.fit.signals` works them out in the database ─────────────


def jobs_features(postings: dict, job_fn=None) -> dict:
    """Each posting's stored features, by `libs.fit.job` or a candidate's own."""
    job_fn = job_fn or fit.job
    return {
        job: job_fn(row["title"], row.get("description"), row.get("language"))
        for job, row in postings.items()
    }


def py_signals(made: dict, features: dict, arrays=None) -> list[float]:
    """[gated title, cos, up, blocked] for one pair, as `libs.fit.signals` and `gated` give them."""
    title_at, vector_at = arrays or fit._arrays(made)
    cos = sum(w * vector_at[p] for p, w in zip(features["vh"], features["vw"], strict=True))
    title = sum(w * title_at[p] for p, w in zip(features["th"], features["tw"], strict=True))
    level = features["level"] if features["level"] is not None else made["level"]
    up = max(0, level - made["level"])
    blocked = 0 if set(features["needs"]) <= set(made["spoken"]) else 1
    return [title * min(1.0, cos / fit.GATE), cos, up, blocked]


def py_x4(made: dict, features: dict, truth: dict) -> dict:
    """Every pair's four signals in Python."""
    arrays = {name: fit._arrays(profile) for name, profile in made.items() if profile}
    return {key: py_signals(made[key[0]], features[key[1]], arrays[key[0]]) for key in truth}
