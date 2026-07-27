"""Source adapters: direct ATS, board APIs, JobSpy. Lifted nearly as-is from ``pipeline/sources/``.

Kept as one adapter per source with a common shape so that adding a board is a file, not a
refactor, and so a single misbehaving source can be disabled without touching the harvest loop.

``fetch.py`` in the parent package keeps ``make_client()`` as the **only** HTTP-client factory
(CODE_STRUCTURE.md §3) — proxy routing, timeouts, and retry policy are decided in one place, which
is what makes "this user's credential, for this user's fetch only" auditable rather than hopeful.
"""
