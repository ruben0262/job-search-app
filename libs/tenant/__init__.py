"""Per-user data access. Every method here takes `user_id`, and none of them makes it optional.

**A deliberate addition to the package list in `CODE_STRUCTURE.md` §2** (recorded 2026-07-27). That
list was written before the repository layer existed and has no home for per-user data access:
`libs/corpus` documents itself as the shared half of the system, and `libs/db` is where the schema
lives. Putting tenant-scoped queries in either would make the shared/per-user boundary — the single
most important line in this architecture — invisible at the call site.

Here it is visible in the import path. `libs.corpus` is shared by definition and its repository
takes no `user_id` at all; `libs.tenant` is per-user by definition and its repository cannot be used
without one. Container-level isolation was traded away deliberately
(`PLATFORM_ARCHITECTURE.md` §2), and this split is part of what replaces it.

The rule from `TENANT_SAFETY.md` §1.3, restated: **`user_id` is a required argument, never an
optional filter a caller may remember to pass.** A query that omits the tenant should fail to
construct rather than quietly return someone else's rows. There is no `get_scores()` here that
optionally filters — only `get_scores(user_id, …)`.

T1 (`TEST_STRATEGY.md`) is the release-blocking test over this package: two users, overlapping jobs,
every read path returns only the caller's rows.
"""
