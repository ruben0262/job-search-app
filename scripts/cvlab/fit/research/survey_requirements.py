"""How the requirement readers agree with the readers' blockers on the 27 seen: each pair the rules call a
missing credential, a degree short, a staff-only or a student-only post, with the label and what readers
named; and the pairs readers blocked that the rules missed.

    PYTHONPATH=. python -m scripts.cvlab.fit.research.survey_requirements
"""

from __future__ import annotations

import statistics
from collections import Counter

from scripts.cvlab.fit.research import harness as h
from scripts.cvlab.fit.research import requirements as rq
from scripts.cvlab.fit.research.variants import kinds_of


def main() -> None:
    data = h.load()
    jobs = {job: rq.posting(row["title"], row.get("description")) for job, row in data.postings.items()}
    people = {name: rq.person(kinds_of(name)) for name in h.SEEN}
    for name in h.SEEN:
        me = people[name]
        print(f"{name:17s} held {me['held']} half {me['half']} degree {me['degree']}")
    keys = [k for k in data.truth if k[0] in h.SEEN]
    gaps = {k: rq.compare(jobs[k[1]], people[k[0]]) for k in keys}
    groups = {
        "missing credential": [k for k in keys if gaps[k]["missing"]],
        "half-held credential": [k for k in keys if gaps[k]["half"]],
        "degree short": [k for k in keys if gaps[k]["degree_gap"]],
        "internal": [k for k in keys if gaps[k]["internal"]],
        "student-only": [k for k in keys if gaps[k]["student"]],
        "none of these": [
            k
            for k in keys
            if not any(
                (
                    gaps[k]["missing"],
                    gaps[k]["half"],
                    gaps[k]["degree_gap"],
                    gaps[k]["internal"],
                    gaps[k]["student"],
                )
            )
        ],
    }
    for group, members in groups.items():
        if not members:
            continue
        blocked = sum(bool(data.rows[k]["blocker_a"] or data.rows[k]["blocker_b"]) for k in members)
        mean = statistics.fmean(data.truth[k] for k in members)
        print(f"\n{group}: {len(members)} pairs, mean label {mean:.2f}, "
              f"a reader named a blocker for {blocked}")
        if group != "none of these":
            for k in members[:40]:
                row = data.rows[k]
                print(
                    f"   {k[0]:17s} {data.truth[k]:4.1f} {gaps[k]['missing_names'] or ''} "
                    f"deg+{gaps[k]['degree_gap']} | {data.postings[k[1]]['title'][:45]} | "
                    f"{(row['blocker_a'] or row['blocker_b'] or '-')[:70]}"
                )
    years = Counter()
    for k in keys:
        asked = jobs[k[1]]["years"]
        years["asked" if asked else "not asked"] += 1
    print("\nyears asked:", dict(years))
    print("\nreaders blocked, rules found nothing:")
    for k in groups["none of these"]:
        row = data.rows[k]
        if row["blocker_a"] and row["blocker_b"]:
            print(f"   {k[0]:17s} {data.truth[k]:4.1f} | {data.postings[k[1]]['title'][:45]} | "
                  f"{row['blocker_a'][:80]}")


if __name__ == "__main__":
    main()
