"""The shared job corpus and its dedup semantics: canonical URL, content key, link normalisation.

Job postings are public, identical for every user, and expensive to acquire; fit is private and
cheap to compute. Splitting the system on that line is the decision everything else follows from
(PLATFORM_ARCHITECTURE.md §1), and this package is the shared half — 0.87 GB steady state against
44 GB for per-user copies at 50 users (§4).

**The dedup rules are load-bearing and tuned.** They are the port of ``pipeline/lib/store.py``
from the reference instance (578 lines, canonical folding and location-aware content keys), which
ROADMAP.md names as the critical path. The regression suite T2 is written against today's SQLite
behaviour *before* the port starts and stays green throughout; dedup that quietly changes
semantics does not crash, it just starts losing or duplicating jobs.

Two invariants this package must never break:

1. **No per-user data on a corpus row.** Labels, scores, feedback, and applied-records reference
   ``job_id``. This is what keeps the storage projection true and account deletion trivial (§8).
2. **Vectors are keyed on a content hash, not ``job_id``.** ``refresh.py`` mutates descriptions on
   live postings, so a ``job_id``-keyed vector silently goes stale against text it no longer
   represents. Store ``text_sha256`` beside the vector and re-embed when it changes (§4).

``filters/`` holds the pure predicate functions lifted from ``pipeline/lib/`` — langfilter,
workfilter, timefilter, salary.
"""
