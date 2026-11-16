"""What the candidate costs against the current score: reading a posting (once, with its text), making a
profile (once per change of documents), and the comparison in the database (every page and filter), on the
local mongod.

    PYTHONPATH=. python -m scripts.cvlab.fit.research.costs
"""

from __future__ import annotations

import statistics
import time

from libs import fit
from scripts.cvlab.fit.research import candidate as c
from scripts.cvlab.fit.research import harness as h
from scripts.cvlab.fit.research import requirements as rq
from scripts.cvlab.fit.research import variants as v

COPIES = 17  # 876 frozen postings, 17 times over: about the 15,000 the earlier timings used


def timed(fn, rounds: int = 1) -> float:
    started = time.perf_counter()
    for _ in range(rounds):
        fn()
    return (time.perf_counter() - started) / rounds


def main() -> None:
    data = h.load()
    rows = list(data.postings.values())
    names = [*h.SEEN, *h.FRESH, *h.FRESH2]
    current_job = timed(lambda: [fit.job(r["title"], r.get("description"), r.get("language")) for r in rows])
    extra_job = timed(lambda: [rq.posting(r["title"], r.get("description")) for r in rows])
    print(
        f"a posting: current features {current_job / len(rows) * 1000:.2f} ms; the requirement reading adds "
        f"{extra_job / len(rows) * 1000:.2f} ms ({len(rows)} postings)"
    )
    current_person = timed(lambda: [fit.person(v.kinds_of(n), v.name_of(n)) for n in names])
    new_person = timed(lambda: [c.profile(n) for n in names])
    print(
        f"a profile: current {current_person / len(names) * 1000:.1f} ms, "
        f"candidate {new_person / len(names) * 1000:.1f} ms ({len(names)} people)"
    )
    feats = {job: c.features(row) for job, row in data.postings.items()}
    groups = [len(f["req"]) for f in feats.values()]
    print(
        f"stored per posting: {statistics.fmean(groups):.2f} credential groups on average, "
        f"{sum(g > 0 for g in groups)} of {len(groups)} postings with any; staff-only "
        f"{sum(f['staff'] for f in feats.values())}, student-only {sum(f['student'] for f in feats.values())}"
    )
    has = [len(c.profile(n)["has"]) for n in names]
    print(f"stored per person: {statistics.fmean(has):.1f} credentials on average (most {max(has)})")
    db = h.database()
    db.jobs.drop()
    docs = [{"_id": job * 100 + copy, "fit": feats[job]} for job in feats for copy in range(COPIES)]
    db.jobs.insert_many(docs)
    made_new, made_old = c.profile("nurse"), data.made["nurse"]
    x = {k: t for k, t in data.truth.items() if k[0] in h.SEEN}
    seen_x = h.py_x4({n: c.profile(n) for n in h.SEEN}, feats, x)
    model = c.as_model(c.fitted(seen_x, data.truth, list(seen_x)))
    for title, expression in (
        ("current", fit.expression(made_old)),
        ("candidate", c.expression(made_new, model)),
    ):
        times = []
        for _ in range(5):
            started = time.perf_counter()
            list(db.jobs.aggregate([{"$project": {"_id": 1, "s": expression}}]))
            times.append(time.perf_counter() - started)
        print(
            f"the comparison, {title}: {statistics.median(times) * 1000:.0f} ms for {len(docs)} postings "
            f"(median of 5)"
        )
    db.client.drop_database(h.SCRATCH)


if __name__ == "__main__":
    main()
