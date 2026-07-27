"""Feature construction and per-user rubric/profile rendering.

This package holds **the one module that must not be duplicated** (CODE_STRUCTURE.md §4).
``features.py`` carries a constraint from its own docstring in the reference instance:

    training and serving MUST build these identically, so both import THIS one function.
    Never inline a second copy.

``services/trainer`` and ``services/scorer`` are separate containers that must agree byte-for-byte
on feature construction. Two copies that drift do not crash — they silently score against
different semantics, which is strictly worse than an outage because nothing reports it.

Three protections, all required together:

1. It lives here and is imported by both. Never copied.
2. ``FEATURE_NAMES`` order is platform-owned and global; only the per-user token lists vary
   (PROFILE_CAPTURE_PLAN.md §6.3).
3. The trained artifact records ``profile_generation`` and ``feature_schema_version``, and the
   scorer **fails closed** on mismatch rather than serving — the same discipline the embedder
   already applies to ``(model, dim, version)``.

This is the one place where "independent services" yields to "these two must be in lockstep", and
the version assert is what makes that safe.

Rendering lives here too — the per-user rubric and facts block generated from a captured profile
(PROFILE_CAPTURE_PLAN.md §6.1). Rendering is deterministic code; the interview that produces its
input is not, and belongs to ``services/capture``.
"""
