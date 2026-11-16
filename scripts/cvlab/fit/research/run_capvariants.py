"""Which blockers the cap should hold, with the role phrases: a missing credential, a staff-only or
student-only post (the candidate), and a language they lack too; and the cap's height. 27 seen, left out one
at a time, paired with the role phrases alone and with the current score.

    PYTHONPATH=. python -m scripts.cvlab.fit.research.run_capvariants
"""

from __future__ import annotations

from scripts.cvlab.fit.research import harness as h
from scripts.cvlab.fit.research.combined import ROLES, blockers, x_for


def main(names=h.SEEN) -> None:
    data = h.load()
    base = h.baseline(data, names)
    gaps = blockers(data, names)
    x = x_for(data, names, ROLES)
    roles = h.lopo(x, data, names)

    def cap(height, language):
        def post(key, score, xs):
            e = gaps[key]
            hit = e["missing"] or e["internal"] or e["student"] or (language and xs[3])
            return min(score, height + 1e-3 * score) if hit else score

        return post

    for height in (3.5, 4.0, 4.5):
        for language in (False, True):
            judged = h.lopo(x, data, names, post=cap(height, language))
            h.show(
                f"cap {height}{' + language' if language else ''} vs roles", judged, roles, data.truth, names
            )
            h.show(
                f"cap {height}{' + language' if language else ''} vs current", judged, base, data.truth, names
            )


if __name__ == "__main__":
    main()
