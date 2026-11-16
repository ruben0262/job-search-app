"""The two readers against each other on a group of people: how far apart, and the pairs three or more apart.

PYTHONPATH=. python -m scripts.cvlab.fit.research.agreement2 <name,name,...>"""

from __future__ import annotations

import statistics
import sys

from scripts.cvlab.fit.build import _readings
from scripts.cvlab.fit.evaluate import _kappa, measure, report

names = sys.argv[1].split(",")
a, b = _readings("a"), _readings("b")
keys = sorted(key for key in a if key[0] in names)
assert keys and all(key in b for key in keys) and len(keys) == sum(1 for key in b if key[0] in names)
gaps = [a[key]["fit"] - b[key]["fit"] for key in keys]
print(
    f"{len(keys)} pairs: exact {sum(g == 0 for g in gaps) / len(gaps):.2f}, within 1 "
    f"{sum(abs(g) <= 1 for g in gaps) / len(gaps):.2f}, "
    f"within 2 {sum(abs(g) <= 2 for g in gaps) / len(gaps):.2f}, "
    f"3 or more apart {sum(abs(g) >= 3 for g in gaps)}; mean a-b {statistics.fmean(gaps):+.2f}, "
    f"mean |a-b| {statistics.fmean(abs(g) for g in gaps):.2f}"
)
print(f"quadratic weighted kappa {_kappa([a[k]['fit'] for k in keys], [b[k]['fit'] for k in keys]):.3f}")
report(
    "rater a against rater b",
    measure({k: float(a[k]["fit"]) for k in keys}, {k: float(b[k]["fit"]) for k in keys}, names, scale=True),
)
for key in keys:
    if abs(a[key]["fit"] - b[key]["fit"]) >= 3:
        print(f"  disputed {key}: a {a[key]['fit']} b {b[key]['fit']}")
