"""Multi-tenant model serving — N user models in an LRU, hot-swapped without restart.

The reference instance loads exactly one artifact at startup and needs a container recreate to
pick up a retrain. Neither survives multi-tenancy (PLATFORM_ARCHITECTURE.md §7).

- Artifacts are per user (``models/{user_id}/scorer.joblib``), written atomically by the trainer
  (temp + rename, ``.bak`` of the previous — already the existing behaviour).
- An **LRU of loaded models** bounded by count *and* RSS. A classifier over 768-dim vectors is a
  few MB, so dozens fit; the ~1.9 GB that dominates the old process was the *embedder*, which is
  no longer in it at all.
- **Hot-swap:** the trainer bumps ``model_version`` on the user row, the scorer compares before
  serving and reloads on change.

Imports ``libs.scoring`` for feature construction and **never** builds features itself — it must
agree byte-for-byte with the trainer (CODE_STRUCTURE.md §4). It **fails closed** on a
``profile_generation`` / ``feature_schema_version`` / embedder ``(model, dim, version)`` mismatch:
a 503 is recoverable, a silently wrong score is not.

Restart blast radius: new jobs queue unscored and drain on restart.
"""
