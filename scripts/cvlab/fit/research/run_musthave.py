"""Candidate 6, must-have coverage done line by line: each line a posting requires
(`requirements.lines_by_kind`) is met when the person's documents have one of its telling words (rarer than
`RARE`, less the words every requirement uses); the feature is the share of required lines met, or how many
are not. The earlier attempt (the share of every requirement-section word they have) measured 0.640 against
0.661.

    PYTHONPATH=. python -m scripts.cvlab.fit.research.run_musthave
"""

from __future__ import annotations

from libs import fit
from libs.fit.text import NOISE, words
from scripts.cvlab.fit.research import harness as h
from scripts.cvlab.fit.research import requirements as rq

# Words every requirement line uses, which say nothing about what is required.
GENERIC = frozenset(
    words("""experience experienced ability able strong excellent good great proven skills skill
knowledge working work team communication written verbal understanding demonstrable demonstrated relevant
previous similar role environment ideally essential required requirement must minimum years year plus
including level high highly attention detail problem solving motivated organised organized self starter
fluent proficiency proficient familiarity familiar background track record degree qualification qualified
equivalent desirable
preferred candidate candidates looking need needs will would should have has please apply""")
)


def line_words(line: str, language: str | None, rare: float) -> list[str]:
    return [
        w
        for w in dict.fromkeys(words(line))
        if w not in NOISE and w not in GENERIC and fit.rarity(w, language) >= rare
    ]


def coverage(posting: dict, vocabulary: set[str], rare: float, explicit_only: bool) -> tuple[float, int, int]:
    """(share of required lines met, lines not met, lines read) for one person and posting."""
    lines = [line for line, kind in rq.lines_by_kind(posting.get("description")) if kind == "required"]
    if explicit_only:
        lines = [line for line in lines if rq._MUST.search(line)]
    read = [line_words(line, posting.get("language"), rare) for line in lines]
    read = [found for found in read if found]
    if not read:
        return 1.0, 0, 0
    met = sum(any(w in vocabulary for w in found) for found in read)
    return met / len(read), len(read) - met, len(read)


def main(names=h.SEEN) -> None:
    data = h.load()
    base = h.baseline(data, names)
    vocab = {name: set(data.made[name]["vector"]) for name in names}
    keys = [k for k in data.truth if k[0] in names]
    for rare in (1.5, 2.5, 3.5):
        for explicit in (False, True):
            cov = {k: coverage(data.postings[k[1]], vocab[k[0]], rare, explicit) for k in keys}
            read = sum(1 for k in keys if cov[k][2])
            for label, extra in (
                ("share met", lambda c: [c[0]]),
                ("lines unmet", lambda c: [min(5, c[1])]),
                ("share, unmet", lambda c: [c[0], min(5, c[1])]),
            ):
                x = {k: [*data.x4[k], *extra(cov[k])] for k in keys}
                h.show(
                    f"rare {rare} {'must' if explicit else 'req'} {label} ({read})"[:38],
                    h.lopo(x, data, names),
                    base,
                    data.truth,
                    names,
                )


if __name__ == "__main__":
    main()
