"""The held-out pairs whose shown number the candidate changed most, with why: the cap, or the title signal
moved by the role phrases. Read after `candidate fresh`; nothing is fitted or chosen here.

    PYTHONPATH=. python -m scripts.cvlab.fit.research.examples [N]
"""

from __future__ import annotations

import sys

from scripts.cvlab.fit.research import candidate as c
from scripts.cvlab.fit.research import harness as h
from scripts.cvlab.fit.research import requirements as rq


def main(count: int) -> None:
    data = h.load()
    held_out = [*h.FRESH, *h.FRESH2]
    new, old = c.holdout(data, h.SEEN, held_out)
    names = [*h.SEEN, *held_out]
    made, _, x, caps = c.build(data, names)
    old_x = h.py_x4(data.made, h.jobs_features(data.postings), {k: data.truth[k] for k in x})
    changed = sorted(new, key=lambda k: (-abs(round(new[k]) - round(old[k])), -abs(new[k] - old[k])))
    for k in changed[:count]:
        row, posting = data.rows[k], data.postings[k[1]]
        why = []
        if caps[k]:
            asked = rq.posting(posting["title"], posting.get("description"))
            missing = [g for g in asked["required"] if not set(g) & set(made[k[0]]["has"])]
            why.append(
                f"capped at 4: {'missing ' + str(missing) if missing else ''}"
                f"{' staff only' if asked['internal'] else ''}{' students only' if asked['student'] else ''}"
            )
        if abs(x[k][0] - old_x[k][0]) > 0.01:
            why.append(f"title signal {old_x[k][0]:.2f} -> {x[k][0]:.2f}")
        print(
            f"{k[0]:18s} {posting['title'][:58]:58s} old {round(old[k]):2d} new {round(new[k]):2d} "
            f"label {data.truth[k]:4.1f}  [{'; '.join(why) or 'weights and steps refitted'}]\n"
            f"{'':18s} readers: {row['why_a'][:150]}"
        )


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 12)
