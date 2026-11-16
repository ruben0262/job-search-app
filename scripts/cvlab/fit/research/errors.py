"""Where the current score's error is on the 27 people seen, left out one at a time: by label band, by sector,
by the kind of blocker readers named, by how a posting was drawn; and the largest misses with the readers'
reasons. The fresh 12 are not read here.

    PYTHONPATH=. python -m scripts.cvlab.fit.research.errors [--misses N]
"""

from __future__ import annotations

import re
import statistics
import sys
from collections import defaultdict

from scripts.cvlab.fit.research import harness as h

BLOCKER_KINDS = (
    ("language", r"language|german|dutch|french|polish|spanish|italian|english|hungarian|fluent|speak"),
    (
        "registration/licence",
        r"registration|registered|licen[cs]e|nmc|nmbi|hcpc|card|clearance|cscs|sia|dbs|"
        r"cpc|adr|forklift|flt|permit|gas safe|18th|mot|pin\b",
    ),
    (
        "qualification/degree",
        r"degree|qualified|qualification|acca|aca\b|cima|meister|solicitor|diploma|"
        r"certificate|certified|cipd|sqe|lpc|chartered|pgce|qts|nvq|level \d|bsc|msc",
    ),
    ("years/seniority", r"year|senior|experience"),
    ("eligibility", r"internal|only|staff|categor|male|female|student|enrolled|graduate scheme"),
)


def kind_of(text: str) -> str:
    text = text.lower()
    return next((name for name, pattern in BLOCKER_KINDS if re.search(pattern, text)), "other")


def table(title: str, groups: dict, judged: dict, truth: dict) -> None:
    total = sum((round(judged[k]) - truth[k]) ** 2 for k in judged)
    print(f"\n{title}")
    for name, keys in sorted(groups.items(), key=lambda item: str(item[0])):
        if not keys:
            continue
        err = [round(judged[k]) - truth[k] for k in keys]
        print(
            f"  {name!s:26s} n {len(keys):3d}  mean error {statistics.fmean(err):+.2f}  "
            f"mean |error| {statistics.fmean(abs(e) for e in err):.2f}  "
            f"share of squared error {sum(e * e for e in err) / total:5.1%}"
        )


def main(misses: int) -> None:
    data = h.load()
    judged = h.baseline(data)
    truth = data.truth
    h.absolute("current score, left out one at a time", judged, truth)
    bands = defaultdict(list)
    for k in judged:
        bands[f"label {int(truth[k] + 0.5) // 2 * 2 - 1 if truth[k] >= 1.5 else 1}"].append(k)
    bands = defaultdict(list)
    for k in judged:
        lab = truth[k]
        band = (
            "1-2"
            if lab < 2.75
            else "3-4"
            if lab < 4.75
            else "5-6"
            if lab < 6.75
            else "7-8"
            if lab < 8.75
            else "9-10"
        )
        bands[band].append(k)
    table("by label band", bands, judged, truth)
    sectors = defaultdict(list)
    for k in judged:
        sectors[h.sector_of(k[0])].append(k)
    table("by sector", sectors, judged, truth)
    blockers = defaultdict(list)
    for k in judged:
        row = data.rows[k]
        both = row["blocker_a"] and row["blocker_b"]
        one = bool(row["blocker_a"]) != bool(row["blocker_b"])
        if both:
            blockers["both: " + kind_of(row["blocker_a"] + " | " + row["blocker_b"])].append(k)
        elif one:
            blockers["one: " + kind_of((row["blocker_a"] or "") + (row["blocker_b"] or ""))].append(k)
        else:
            blockers["none"].append(k)
    table("by blocker readers named", blockers, judged, truth)
    drawn = defaultdict(list)
    for k in judged:
        drawn[data.rows[k]["drawn"]].append(k)
    table("by how the posting was drawn", drawn, judged, truth)
    people = defaultdict(list)
    for k in judged:
        people[k[0]].append(k)
    table("by person", people, judged, truth)
    if misses:
        print(f"\nthe {misses} largest misses")
        for k in sorted(judged, key=lambda k: -abs(round(judged[k]) - truth[k]))[:misses]:
            row, posting = data.rows[k], data.postings[k[1]]
            title, cos, up, blocked = data.x4[k]
            print(
                f"  {k[0]:17s} {k[1]:6d} shown {round(judged[k]):2d} label {truth[k]:4.1f}  "
                f"t {title:.2f} c {cos:.3f} up {up} bl {blocked}  | {posting['title'][:60]}\n"
                f"      a: {row['why_a'][:200]}"
                f"\n      blocker: {row['blocker_a']} / {row['blocker_b']}"
            )


if __name__ == "__main__":
    main(int(sys.argv[sys.argv.index("--misses") + 1]) if "--misses" in sys.argv else 0)
