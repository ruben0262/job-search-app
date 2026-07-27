"""Queue worker: labelling and retraining. Concurrency 1, CPU-fenced, debounced.

Concurrency 1 is not a simplification — it is the memory and CPU budget. Training is the spikiest
thing on a box that also serves scoring, and the 2026-07-24 incident was a memory crunch with far
less running than the platform will have (PLATFORM_ARCHITECTURE.md §11). Debounce so that ten
labels in a minute cause one retrain, not ten.

Also owns cold start (COLD_START_PLAN.md): pool selection → backfill labelling → first train, with
stage progression visible to the user, because a new account that looks idle for twenty minutes
reads as broken.

``labelling.py`` is the queue-driven port of ``scorer/label.py``. It spends the **user's own**
Claude credential, so failures are user-visible and quota-bounded, not silent.

Imports ``libs.scoring`` for features — the same module the scorer imports, never a second copy
(CODE_STRUCTURE.md §4). Writes artifacts atomically and bumps ``model_version`` so the scorer
hot-swaps.

Restart blast radius: retrains queue up.
"""
