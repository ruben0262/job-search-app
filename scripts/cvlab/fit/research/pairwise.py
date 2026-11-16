"""Weights fitted to order each person's list rather than to every label pooled: a logistic loss on the
difference of two of one person's postings `gap` or more apart, on standardised signals with a light penalty,
by Newton's method; then the scale and intercept by least squares on the labels and the by-rank steps, as
`model.py` makes them. Deterministic, and the same linear sum the site runs: only the weights differ.

    PYTHONPATH=. python -m scripts.cvlab.fit.research.pairwise
"""

from __future__ import annotations

import numpy as np

from scripts.cvlab.fit.evaluate import GAP
from scripts.cvlab.fit.research import harness as h


def fit_pairwise(x: dict, truth: dict, keys: list, gap: float = GAP, l2: float = 1e-3) -> h.Fitted:
    matrix = np.array([x[k] for k in keys], dtype=float)
    mean, std = matrix.mean(axis=0), matrix.std(axis=0)
    std[std == 0] = 1.0
    z = (matrix - mean) / std
    labels = np.array([truth[k] for k in keys])
    people = np.array([k[0] for k in keys])
    rows = []
    for name in dict.fromkeys(people):
        index = np.flatnonzero(people == name)
        for a in range(len(index)):
            for b in range(a + 1, len(index)):
                i, j = index[a], index[b]
                if abs(labels[i] - labels[j]) >= gap:
                    rows.append(z[i] - z[j] if labels[i] > labels[j] else z[j] - z[i])
    d = np.array(rows)
    w = np.zeros(z.shape[1])
    for _ in range(50):
        s = d @ w
        p = 1.0 / (1.0 + np.exp(-s))
        grad = -d.T @ (1.0 - p) / len(d) + l2 * w
        hess = (d * (p * (1.0 - p))[:, None]).T @ d / len(d) + l2 * np.eye(len(w))
        step = np.linalg.solve(hess, grad)
        w -= step
        if np.abs(step).max() < 1e-9:
            break
    raw = z @ w
    scale = np.cov(raw, labels, bias=True)[0, 1] / (raw.var() or 1.0)
    weights = [*(w * scale / std), float(labels.mean() - scale * raw.mean() - (w * scale / std) @ mean)]
    weights = [round(float(value), 4) for value in weights]
    return h.Fitted(weights, h.by_rank([h.raw_of(weights, x[k]) for k in keys], [truth[k] for k in keys]))


def main() -> None:
    data = h.load()
    base = h.baseline(data)
    x4 = {k: value for k, value in data.x4.items() if k[0] in h.SEEN}
    for gap in (1.0, 2.0, 3.0):
        for l2 in (1e-3, 1e-1):
            h.show(
                f"pairwise gap {gap} l2 {l2}",
                h.lopo(x4, data, fitter=lambda a, b, c, g=gap, r=l2: fit_pairwise(a, b, c, g, r)),
                base,
                data.truth,
            )


if __name__ == "__main__":
    main()
