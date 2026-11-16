"""Candidate 5's second half: the posting's title against the person's most recent roles only, and whether the
job a title names (its occupation head: "engineer" of "Data Warehouse Engineer") is one the person has held,
each beside the four signals with the role phrases, on the 27 seen, left out one at a time, paired with the
role phrases alone (the comparison isolates the new feature) and with the current score.

    PYTHONPATH=. python -m scripts.cvlab.fit.research.run_recent
"""

from __future__ import annotations

from libs import fit
from libs.documents.read import fold
from libs.fit.text import NOISE, words
from scripts.cvlab.fit.research import harness as h
from scripts.cvlab.fit.research import variants as v
from scripts.cvlab.fit.research.combined import ROLES
from scripts.cvlab.fit.research.roles import _TOKEN, core_parts, head_of, role_phrases


def heads(text: str) -> set[str]:
    """The occupation heads a text names, a compound's as its head ("transportplanner" is a "planner")."""
    return {found for token in _TOKEN.findall(fold(text)) if (found := head_of(token))}


def main(names=h.SEEN) -> None:
    data = h.load()
    base = h.baseline(data, names)
    made = v.profiles(names, **ROLES)
    feats = h.jobs_features(data.postings)
    truth = {k: t for k, t in data.truth.items() if k[0] in names}
    x_roles = h.py_x4(made, feats, truth)
    roles_only = h.lopo(x_roles, data, names)
    phrases = {
        name: [p for text in v.kinds_of(name).get("cv", []) for p in role_phrases(text, v.name_of(name))]
        for name in names
    }
    person_heads = {
        name: set().union(*(heads(p) for p in phrases[name])) if phrases[name] else set() for name in names
    }
    job_heads = {job: heads(" ".join(core_parts(row["title"]))) for job, row in data.postings.items()}
    for recent in (1, 2, 3):
        recent_words = {
            name: {w for p in phrases[name][:recent] for w in words(p) if w not in NOISE} for name in names
        }

        def recent_match(key, n=recent_words):
            row = data.postings[key[1]]
            title = [w for w in dict.fromkeys(words(" ".join(core_parts(row["title"])))) if w not in NOISE]
            if not title:
                return 0.0
            total = sum(fit.rarity(w, row.get("language")) for w in title)
            return sum(fit.rarity(w, row.get("language")) for w in title if w in n[key[0]]) / total

        x = {k: [*x_roles[k], recent_match(k)] for k in truth}
        judged = h.lopo(x, data, names)
        h.show(f"roles + recent {recent} title match", judged, roles_only, data.truth, names)
        h.show("  (against the current score)", judged, base, data.truth, names)
    x = {k: [*x_roles[k], float(bool(job_heads[k[1]] & person_heads[k[0]]))] for k in truth}
    judged = h.lopo(x, data, names)
    h.show("roles + same occupation head", judged, roles_only, data.truth, names)
    h.show("  (against the current score)", judged, base, data.truth, names)
    x = {
        k: [*x_roles[k], float(bool(job_heads[k[1]]) and not job_heads[k[1]] & person_heads[k[0]])]
        for k in truth
    }
    judged = h.lopo(x, data, names)
    h.show("roles + another occupation head", judged, roles_only, data.truth, names)


if __name__ == "__main__":
    main()
