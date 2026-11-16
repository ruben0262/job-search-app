"""How well a fit score agrees with people who read the postings, on the frozen set (`build.py`).

    python -m scripts.cvlab.fit.evaluate agreement           # the two raters against each other
    python -m scripts.cvlab.fit.evaluate score [--split dev|test|all] [--scorer module:function ...]

**What is judged against what.** Every pair (a person, a posting from their own list) has two readings, by two
raters who never saw each other's (`rubric.md`). The label is their mean, unless they were three or more
apart: then a third reading decides (`labels.jsonl`, `third`). How far the two raters are from each other is
the most anyone can expect a score to be from the label, so it is printed beside every score.

**The measures**, each per person and then averaged over people, since a score orders one person's list and is
never compared across people:

- `rho`: rank correlation of the score with the label over the person's postings (Spearman, ties averaged).
- `pairs`: of two of their postings whose labels are at least `GAP` apart, how often the score puts the better
  one first (a tie in the score counts half).
- `top5`: the mean label of the five postings the score puts first, against the best five's.
- `good P/R`: a filter of "fit 7 or more" by the score, against postings labelled 7 or more.
- `poor P/R`: "fit 3 or less", against postings labelled 3 or less.
- `mae`, `±1`, `bias`: how far the number itself is from the label, for a score on the 1 to 10 scale.

A 95% interval for `rho` and `pairs` comes from resampling people. People are split in two halves of mixed
trades (`SPLIT`): a score is tuned on `dev` and reported on `test`, never the other way round.
"""
from __future__ import annotations

import argparse
import importlib
import random
import statistics
from collections import defaultdict
from collections.abc import Callable

from scripts.cvlab.fit.build import FIT, documents, people, read_jsonl

RATINGS = FIT.parent / "out" / "fit" / "labels"
LABELS = FIT / "labels.jsonl"
# Two postings at least this far apart in their labels are a clear call to get right.
GAP = 2
# Each half holds a mix of trades, countries and languages; nobody is moved between them after scores are
# seen.
SPLIT = {
    "dev": ("warehouse", "transport-planner", "nurse", "sales", "chef", "hotel-reception", "accountant",
            "elektriker", "mech-graduate", "paralegal", "recruiter", "marketing", "backend-dev",
            "project-manager"),
    "test": ("driver", "care-worker", "account-executive", "school-leaver", "konyvelo", "vehicle-tech",
             "cnc-operator", "office-senior", "claims-handler", "teacher", "frontend-junior", "data-analyst",
             "site-manager"),
    # Drawn and read after the halves above had been looked at (2026-11-16): the held-out set since then.
    "fresh": ("housekeeper", "barista", "store-manager", "logistics", "maintenance", "hr-advisor",
              "kundenservice", "underwriter", "it-support", "civil-engineer", "cuoco", "events"),
    # Written, drawn and read blind on 2026-11-16, night, while candidates were built on the 27 above: nothing
    # was tuned on them, and their documents were not read by whoever built the candidates.
    "fresh2": ("security-officer", "dental-nurse", "lagerist", "delivery-driver", "teaching-assistant",
               "payroll", "elektromonteur", "commessa", "ugyfelszolgalat", "administrativa", "plumber",
               "social-worker"),
}
SECTOR = {
    "warehouse": "logistics", "driver": "logistics", "transport-planner": "logistics",
    "nurse": "health and care", "care-worker": "health and care",
    "sales": "sales", "account-executive": "sales",
    "chef": "hospitality", "school-leaver": "hospitality", "hotel-reception": "hospitality",
    "konyvelo": "finance", "accountant": "finance",
    "elektriker": "trades and making", "vehicle-tech": "trades and making",
    "cnc-operator": "trades and making",
    "mech-graduate": "trades and making",
    "office-senior": "office, legal, HR", "paralegal": "office, legal, HR", "recruiter": "office, legal, HR",
    "claims-handler": "office, legal, HR",
    "teacher": "education", "marketing": "marketing",
    "backend-dev": "IT", "frontend-junior": "IT", "data-analyst": "IT",
    "project-manager": "projects and building", "site-manager": "projects and building",
    "housekeeper": "hospitality", "barista": "hospitality", "cuoco": "hospitality", "store-manager": "sales",
    "logistics": "logistics", "maintenance": "trades and making", "hr-advisor": "office, legal, HR",
    "kundenservice": "office, legal, HR", "underwriter": "finance", "it-support": "IT",
    "civil-engineer": "projects and building", "events": "marketing",
    "security-officer": "security", "dental-nurse": "health and care", "lagerist": "logistics",
    "delivery-driver": "logistics", "teaching-assistant": "education", "payroll": "finance",
    "elektromonteur": "trades and making", "commessa": "sales", "ugyfelszolgalat": "office, legal, HR",
    "administrativa": "office, legal, HR", "plumber": "trades and making", "social-worker": "health and care",
}


# ── reading the labels ──────────────────────────────────────────────────────────────────────────

def ratings(rater: str) -> dict[tuple[str, int], dict]:
    """One rater's readings, by (person, job). Every pair must have exactly one."""
    found: dict[tuple[str, int], dict] = {}
    for path in sorted((RATINGS / rater).glob("*.jsonl")):
        for row in read_jsonl(path):
            key = (row["person"], int(row["job"]))
            if key in found:
                raise SystemExit(f"rater {rater} judged {key} twice")
            if not (isinstance(row["fit"], int) and 1 <= row["fit"] <= 10):
                raise SystemExit(f"rater {rater}: {key} has fit {row['fit']!r}")
            found[key] = row
    expected = {(pair["person"], pair["job"]) for pair in read_jsonl(FIT / "pairs.jsonl")}
    if missing := expected - set(found):
        raise SystemExit(f"rater {rater} has no reading for {len(missing)} pair(s), "
                         f"e.g. {sorted(missing)[:3]}")
    if extra := set(found) - expected:
        raise SystemExit(f"rater {rater} judged {len(extra)} pair(s) not in the set, "
                         f"e.g. {sorted(extra)[:3]}")
    return found


def labels() -> dict[tuple[str, int], float]:
    """The label of each pair (`labels.jsonl`)."""
    return {(row["person"], row["job"]): row["label"] for row in read_jsonl(LABELS)}


# ── the measures ────────────────────────────────────────────────────────────────────────────────

def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    at = 0
    while at < len(order):
        end = at
        while end + 1 < len(order) and values[order[end + 1]] == values[order[at]]:
            end += 1
        for index in order[at:end + 1]:
            ranks[index] = (at + end) / 2 + 1
        at = end + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    sx = sum((value - mx) ** 2 for value in rx) ** 0.5
    sy = sum((value - my) ** 2 for value in ry) ** 0.5
    if not sx or not sy:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True)) / (sx * sy)


def pair_order(scores: list[float], truth: list[float]) -> tuple[float, int]:
    """Of the pairs whose labels are `GAP` apart, the share the score orders the same way, and how many."""
    right = total = 0.0
    for i in range(len(truth)):
        for j in range(i + 1, len(truth)):
            if abs(truth[i] - truth[j]) >= GAP:
                total += 1
                agree = (scores[i] - scores[j]) * (truth[i] - truth[j])
                right += 1 if agree > 0 else 0.5 if agree == 0 else 0
    return (right / total if total else float("nan")), int(total)


def _prf(chosen: list[bool], actual: list[bool]) -> tuple[int, int, int]:
    hits = sum(c and a for c, a in zip(chosen, actual, strict=True))
    return hits, sum(chosen), sum(actual)


def measure(scored: dict[tuple[str, int], float], truth: dict[tuple[str, int], float],
            names: list[str], *, scale: bool) -> dict:
    """Every measure, per person and over the people in `names`."""
    per: dict[str, dict] = {}
    good = [0, 0, 0]
    poor = [0, 0, 0]
    errors = []
    for name in names:
        keys = sorted(key for key in truth if key[0] == name)
        if not keys:
            continue
        s, t = [scored[key] for key in keys], [truth[key] for key in keys]
        order, n = pair_order(s, t)
        best = sorted(t, reverse=True)[:5]
        mine = [label for _, label in sorted(zip(s, t, strict=True), key=lambda each: -each[0])[:5]]
        per[name] = {"rho": spearman(s, t), "pairs": order, "n_pairs": n,
                     "top5": statistics.fmean(mine), "best5": statistics.fmean(best)}
        if scale:
            # The number a person sees is the score rounded; the order is the score's own.
            shown = [round(x) for x in s]
            for _band, counts, chosen, actual in (
                    ("good", good, [x >= 7 for x in shown], [y >= 7 for y in t]),
                    ("poor", poor, [x <= 3 for x in shown], [y <= 3 for y in t])):
                for at, value in enumerate(_prf(chosen, actual)):
                    counts[at] += value
            errors += [x - y for x, y in zip(shown, t, strict=True)]
    rhos = [each["rho"] for each in per.values() if each["rho"] is not None]
    pairs = [each["pairs"] for each in per.values() if each["n_pairs"]]
    out = {"people": per, "rho": statistics.fmean(rhos), "pairs": statistics.fmean(pairs),
           "top5": statistics.fmean(each["top5"] for each in per.values()),
           "best5": statistics.fmean(each["best5"] for each in per.values()),
           "rho_ci": _interval(per, "rho"), "pairs_ci": _interval(per, "pairs")}
    if scale:
        out.update(good=good, poor=poor, mae=statistics.fmean(abs(e) for e in errors),
                   within1=sum(abs(e) <= 1 for e in errors) / len(errors), bias=statistics.fmean(errors))
    return out


def _interval(per: dict[str, dict], field: str, rounds: int = 2_000) -> tuple[float, float]:
    """A 95% interval for the mean over people, by resampling people."""
    values = [each[field] for each in per.values() if each[field] is not None and each[field] == each[field]]
    draw = random.Random(7)  # noqa: S311 - a seeded resample, not a secret
    means = sorted(statistics.fmean(draw.choices(values, k=len(values))) for _ in range(rounds))
    return means[int(rounds * 0.025)], means[int(rounds * 0.975) - 1]


def report(title: str, result: dict) -> None:
    line = (f"{title:34s} rho {result['rho']:.3f} [{result['rho_ci'][0]:.2f}, {result['rho_ci'][1]:.2f}]  "
            f"pairs {result['pairs']:.3f} [{result['pairs_ci'][0]:.2f}, {result['pairs_ci'][1]:.2f}]  "
            f"top5 {result['top5']:.2f} of {result['best5']:.2f}")
    if "mae" in result:
        (gh, gc, ga), (ph, pc, pa) = result["good"], result["poor"]
        line += (f"  good P {gh / gc if gc else float('nan'):.2f} R {gh / ga if ga else float('nan'):.2f}"
                 f"  poor P {ph / pc if pc else float('nan'):.2f} R {ph / pa if pa else float('nan'):.2f}"
                 f"  mae {result['mae']:.2f} ±1 {result['within1']:.2f} bias {result['bias']:+.2f}")
    print(line)


def paired(first: dict, second: dict, names: list[str], title: str, rounds: int = 4_000) -> None:
    """`first` minus `second`, person by person, for each measure: the mean difference, a 95% interval by
    resampling people, and for how many people `first` was better. Two averages with their own intervals hide
    a small difference that is the same for everybody; a paired one does not (CLAUDE.md §3a)."""
    for field in ("rho", "pairs", "top5"):
        diffs = [first["people"][n][field] - second["people"][n][field] for n in names
                 if first["people"][n][field] is not None and second["people"][n][field] is not None]
        draw = random.Random(3)  # noqa: S311 - a seeded resample, not a secret
        means = sorted(statistics.fmean(draw.choices(diffs, k=len(diffs))) for _ in range(rounds))
        print(f"{title}, {field:5s}: {statistics.fmean(diffs):+.3f}  95% [{means[int(rounds * 0.025)]:+.3f}, "
              f"{means[int(rounds * 0.975) - 1]:+.3f}]  "
              f"better for {sum(d > 0 for d in diffs)} of {len(diffs)}")


def by_sector(result: dict) -> None:
    groups: dict[str, list[dict]] = defaultdict(list)
    for name, each in result["people"].items():
        groups[SECTOR[name]].append(each)
    for sector, rows in sorted(groups.items()):
        rhos = [row["rho"] for row in rows if row["rho"] is not None]
        print(f"    {sector:22s} {len(rows)} people  rho {statistics.fmean(rhos):.3f}  "
              f"pairs {statistics.fmean(row['pairs'] for row in rows if row['n_pairs']):.3f}")


# ── the commands ────────────────────────────────────────────────────────────────────────────────

def agreement() -> None:
    """The raters against each other: the ceiling for any score, as the two readings of one pair differ."""
    a, b = ratings("a"), ratings("b")
    keys = sorted(a)
    gaps = [a[key]["fit"] - b[key]["fit"] for key in keys]
    print(f"{len(keys)} pairs: exact {sum(g == 0 for g in gaps) / len(gaps):.2f}, within 1 "
          f"{sum(abs(g) <= 1 for g in gaps) / len(gaps):.2f}, "
          f"within 2 {sum(abs(g) <= 2 for g in gaps) / len(gaps):.2f}, "
          f"3 or more apart {sum(abs(g) >= 3 for g in gaps)}; mean a-b {statistics.fmean(gaps):+.2f}, "
          f"mean |a-b| {statistics.fmean(abs(g) for g in gaps):.2f}")
    print(f"quadratic weighted kappa {_kappa([a[k]['fit'] for k in keys], [b[k]['fit'] for k in keys]):.3f}")
    as_scores = {key: float(a[key]["fit"]) for key in keys}
    truth = {key: float(b[key]["fit"]) for key in keys}
    for split, names in (("all", sorted(people())), *SPLIT.items()):
        report(f"rater a against rater b, {split}", measure(as_scores, truth, list(names), scale=True))
    by_sector(measure(as_scores, truth, sorted(people()), scale=True))


def _kappa(xs: list[int], ys: list[int], top: int = 10) -> float:
    n = len(xs)
    observed = sum((x - y) ** 2 for x, y in zip(xs, ys, strict=True)) / n
    hx, hy = [xs.count(v) / n for v in range(1, top + 1)], [ys.count(v) / n for v in range(1, top + 1)]
    expected = sum(hx[i] * hy[j] * (i - j) ** 2 for i in range(top) for j in range(top))
    return 1 - observed / expected


Scorer = Callable[[str, list[tuple[str, str]], dict, dict], float]


def load_scorer(spec: str) -> tuple[str, Scorer, bool]:
    """`module:function`; the function takes (person, their documents, their search, a posting) and gives a
    number. `scale = True` on the function says the number is on the 1-10 scale itself."""
    module, _, name = spec.partition(":")
    function = getattr(importlib.import_module(module), name)
    return spec, function, getattr(function, "scale", False)


def score(specs: list[str], split: str) -> None:
    truth = labels()
    postings = {row["id"]: row for row in read_jsonl(FIT / "postings.jsonl")}
    searches = people()
    names = sorted(searches) if split == "all" else list(SPLIT[split])
    a, b = ratings("a"), ratings("b")
    report("ceiling: rater a against rater b", measure(
        {key: float(row["fit"]) for key, row in a.items()},
        {key: float(row["fit"]) for key, row in b.items()}, names, scale=True))
    for spec in specs:
        title, function, scale = load_scorer(spec)
        prepare = getattr(function, "prepare", None)
        if prepare:
            prepare(names, postings)
        scored = {key: function(key[0], documents(key[0]), searches[key[0]], postings[key[1]])
                  for key in truth if key[0] in names}
        result = measure(scored, truth, names, scale=scale)
        report(title.split(":")[-1], result)
        by_sector(result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("agreement", "score"))
    parser.add_argument("--split", default="dev", choices=("dev", "test", "all"))
    parser.add_argument("--scorer", action="append", default=[])
    args = parser.parse_args()
    if args.command == "agreement":
        agreement()
    else:
        score(args.scorer or ["scripts.cvlab.fit.baselines:words_found"], args.split)
