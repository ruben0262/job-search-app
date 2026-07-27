"""FastAPI — auth, config, dashboard, admin console. The only service the browser talks to.

Restart blast radius: users cannot log in or edit config; the pipeline keeps running
(PLATFORM_ARCHITECTURE.md §3). Kept small and fast to rebuild for exactly that reason — it is what
gets restarted most.

Implements TENANT_SAFETY.md (isolation, plan-scoped quotas, bounded config schema) and
OPERATIONS_PLAN.md §4b/§5 (health banners, the action-capable admin console with a full audit
trail and **no impersonation**).

Its OpenAPI spec is the source the TypeScript client is generated from at Phase 3
(CODE_STRUCTURE.md §8) — never hand-write that client, or a changed response shape becomes a
runtime ``undefined`` in production instead of a compile error.
"""
