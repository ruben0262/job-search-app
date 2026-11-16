"""The harness against `model.py check`: the Python replica of `libs.fit.expression` gives the database's
numbers.

PYTHONPATH=. python -m scripts.cvlab.fit.research.verify"""

from scripts.cvlab.fit import model as fm
from scripts.cvlab.fit.research import harness as h

data = h.load(keep_db=True)
db = h.database()
judged_py, judged_db = {}, {}
for held in h.SEEN:
    train = [k for k in data.x4 if k[0] in h.SEEN and k[0] != held]
    m = fm.fitted(data.x4, data.truth, train)
    mine = h.fit_linear(data.x4, data.truth, train)
    judged_db |= fm.scores(db, data.made, data.truth, {"weights": m["weights"], "steps": m["steps"]}, [held])
    for k in data.x4:
        if k[0] == held:
            judged_py[k] = mine.score(data.x4[k])
print(
    "max |python - database| over",
    len(judged_db),
    "pairs:",
    max(abs(judged_py[k] - judged_db[k]) for k in judged_db),
)
h.absolute("model.py check (database)", judged_db, data.truth)
h.absolute("python replica, no copy exclusion", judged_py, data.truth)
h.absolute("harness baseline (copies excluded)", h.baseline(data), data.truth)
py = h.py_x4(data.made, h.jobs_features(data.postings), data.truth)
print(
    "max |python signal - database signal|:",
    max(abs(a - b) for key in data.x4 for a, b in zip(py[key], data.x4[key], strict=True)),
)
db.client.drop_database(h.SCRATCH)
