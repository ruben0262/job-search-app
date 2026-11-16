"""Candidates 4 and 7 that need no new reader: the gate between a title and the text, a title-and-text
product, levels below the person ("down", in both directions), a steeper step up, and the fit's penalty. Each
on the 27 seen, left out one at a time, paired with the current score.

    PYTHONPATH=. python -m scripts.cvlab.fit.research.run_shape
"""

from __future__ import annotations

from libs import fit
from scripts.cvlab.fit.research import harness as h


def raw_signals(made: dict, features: dict, arrays) -> dict:
    """The ungated signals of one pair, and the levels either way."""
    title_at, vector_at = arrays
    cos = sum(w * vector_at[p] for p, w in zip(features["vh"], features["vw"], strict=True))
    title = sum(w * title_at[p] for p, w in zip(features["th"], features["tw"], strict=True))
    known = features["level"] is not None
    level = features["level"] if known else made["level"]
    return {
        "title": title,
        "cos": cos,
        "up": max(0, level - made["level"]),
        "down": max(0, made["level"] - level) if known else 0,
        "blocked": 0 if set(features["needs"]) <= set(made["spoken"]) else 1,
        "known": known,
    }


def main() -> None:
    data = h.load()
    names = h.SEEN
    base = h.baseline(data, names)
    feats = h.jobs_features(data.postings)
    arrays = {name: fit._arrays(data.made[name]) for name in names}
    sig = {k: raw_signals(data.made[k[0]], feats[k[1]], arrays[k[0]]) for k in data.truth if k[0] in names}

    def gated(s, gate):
        return s["title"] * min(1.0, s["cos"] / gate)

    def run(title, fn, **kw):
        x = {k: fn(s) for k, s in sig.items()}
        return h.show(title, h.lopo(x, data, names, **kw), base, data.truth, names)

    for gate in (0.01, 0.04, 0.06, 0.08, 0.10, 0.14):
        run(f"gate {gate}", lambda s, g=gate: [gated(s, g), s["cos"], s["up"], s["blocked"]])
    run(
        "+ title x cos",
        lambda s: [gated(s, fit.GATE), s["cos"], s["up"], s["blocked"], s["title"] * s["cos"]],
    )
    run("+ cos squared", lambda s: [gated(s, fit.GATE), s["cos"], s["up"], s["blocked"], s["cos"] ** 2])
    run("+ title squared", lambda s: [gated(s, fit.GATE), s["cos"], s["up"], s["blocked"], s["title"] ** 2])
    run("+ down", lambda s: [gated(s, fit.GATE), s["cos"], s["up"], s["blocked"], s["down"]])
    run("+ down >= 2", lambda s: [gated(s, fit.GATE), s["cos"], s["up"], s["blocked"], float(s["down"] >= 2)])
    run("+ up >= 2", lambda s: [gated(s, fit.GATE), s["cos"], s["up"], s["blocked"], float(s["up"] >= 2)])
    run("up as up >= 1 only", lambda s: [gated(s, fit.GATE), s["cos"], float(s["up"] >= 1), s["blocked"]])
    for penalty in (0.0, 0.3, 3.0, 10.0):
        run(
            f"ridge penalty {penalty}",
            lambda s: [gated(s, fit.GATE), s["cos"], s["up"], s["blocked"]],
            fitter=lambda x, t, k, p=penalty: h.fit_linear(x, t, k, p),
        )


if __name__ == "__main__":
    main()
