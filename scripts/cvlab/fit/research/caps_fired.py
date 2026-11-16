"""Which caps the candidate applied, by group of people and by what fired: the credential groups, staff-only
and student-only, with the readers' labels of the capped pairs. Read after `candidate fresh`; nothing is
chosen here.

    PYTHONPATH=. python -m scripts.cvlab.fit.research.caps_fired
"""

from __future__ import annotations

import statistics
from collections import Counter

from scripts.cvlab.fit.research import candidate as c
from scripts.cvlab.fit.research import harness as h


def main() -> None:
    data = h.load()
    names = [*h.SEEN, *h.FRESH, *h.FRESH2]
    made, feats, _, caps = c.build(data, names)
    for title, group in (("seen 27", h.SEEN), ("fresh 12", h.FRESH), ("fresh2 12", h.FRESH2)):
        keys = [k for k in caps if k[0] in group and caps[k]]
        fired = Counter()
        for k in keys:
            f = feats[k[1]]
            for found in f["req"]:
                if not set(found) & set(made[k[0]]["has"]):
                    fired["/".join(found)] += 1
            if f["staff"]:
                fired["staff only"] += 1
            if f["student"] and not made[k[0]]["student"]:
                fired["students only"] += 1
        labels = [data.truth[k] for k in keys]
        mean = statistics.fmean(labels) if labels else float("nan")
        print(
            f"{title}: {len(keys)} pairs capped of {sum(1 for k in caps if k[0] in group)}, readers' mean "
            f"label {mean:.2f}, at 5 or more {sum(lab >= 5 for lab in labels)}"
        )
        print("   " + ", ".join(f"{name} {n}" for name, n in fired.most_common()))


if __name__ == "__main__":
    main()
