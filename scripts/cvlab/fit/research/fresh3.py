"""The one read of the recommended pair on the third held-out set (`SPLIT["fresh3"]`, weighted to licensed
and registered trades): the candidate exactly as at the research commit (`candidate.py`, its readers and
credential list unchanged) against the current score, both fitted on the 27 seen, paired, on fresh3 alone and
on all 36 held-out people. Then every pair the cap fired on in fresh3, with the readers' label and blocker,
and every fresh3 pair a reader blocked that the cap did not fire on, to report what the list misses.

    PYTHONPATH=. python -m scripts.cvlab.fit.research.fresh3
"""
from __future__ import annotations

from scripts.cvlab.fit.evaluate import SPLIT
from scripts.cvlab.fit.research import candidate as c
from scripts.cvlab.fit.research import harness as h

FRESH3 = list(SPLIT["fresh3"])


def main() -> None:
    data = h.load()
    for title, group in (("fresh3 12", FRESH3), ("all 36 held out", [*h.FRESH, *h.FRESH2, *FRESH3])):
        new, old = c.holdout(data, h.SEEN, group)
        h.show(f"candidate vs current, {title}", new, old, data.truth, group)
        h.absolute(f"  current, {title}", old, data.truth, group)
        h.absolute(f"  candidate, {title}", new, data.truth, group)
    new, old = c.holdout(data, h.SEEN, FRESH3)
    made, feats, _, caps = c.build(data, [*h.SEEN, *FRESH3])
    capped = [k for k in caps if k[0] in FRESH3 and caps[k]]
    print(f"\nfresh3: the cap fired on {len(capped)} of {sum(1 for k in caps if k[0] in FRESH3)} pairs; "
          f"{sum(data.truth[k] >= 5 for k in capped)} of them read 5 or more")
    for k in sorted(capped):
        f, row = feats[k[1]], data.rows[k]
        missing = [g for g in f["req"] if not set(g) & set(made[k[0]]["has"])]
        why = [*(f"missing {'/'.join(g)}" for g in missing), *(["staff only"] if f["staff"] else []),
               *(["students only"] if f["student"] and not made[k[0]]["student"] else [])]
        print(f"  {k[0]:22s} {data.postings[k[1]]['title'][:52]:52s} label {data.truth[k]:4.1f} "
              f"old {round(old[k]):2d} new {round(new[k]):2d}  [{'; '.join(why)}]  has {made[k[0]]['has']}\n"
              f"  {'':22s} readers' blocker: {row['blocker_a']} / {row['blocker_b']}")
    print("\nfresh3 pairs a reader blocked that the cap did not fire on:")
    for k in sorted(k for k in caps if k[0] in FRESH3 and not caps[k]):
        row = data.rows[k]
        if row["blocker_a"] or row["blocker_b"]:
            print(f"  {k[0]:22s} {data.postings[k[1]]['title'][:52]:52s} label {data.truth[k]:4.1f} "
                  f"old {round(old[k]):2d} new {round(new[k]):2d}\n"
                  f"  {'':22s} a: {row['blocker_a']} | b: {row['blocker_b']}")
    print("\nfresh3 pairs whose shown number moved 2 or more:")
    for k in sorted((k for k in new if abs(round(new[k]) - round(old[k])) >= 2),
                    key=lambda k: -abs(round(new[k]) - round(old[k]))):
        print(f"  {k[0]:22s} {data.postings[k[1]]['title'][:52]:52s} old {round(old[k]):2d} "
              f"new {round(new[k]):2d} label {data.truth[k]:4.1f}  capped {caps[k]}")


if __name__ == "__main__":
    main()
