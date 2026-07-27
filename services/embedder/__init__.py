"""TEI + gateway — one resident copy of BGE serving every tenant's scoring *and* training.

**The only service that already exists.** Built, tested, and deployed before the platform design
was written; relocated here from the repo root in Phase 1 step 0a as a source-tree move, with the
running container, its image, and its URL untouched (CODE_STRUCTURE.md §6).

It is also the service the rest of the platform copies its contract discipline from
(PLATFORM_ARCHITECTURE.md §5): ``/health`` reports what a live probe of the TEI backend confirmed,
not what the ``.env`` asserts, and callers assert ``(model, dim, version)`` before trusting a
vector. On mismatch the gateway stays unhealthy permanently and both embed endpoints 503, so a
config drift becomes a visible outage instead of silently wrong scores from a different vector
space. Generalising that — a version mismatch is a 503, never a best-effort degraded answer — is
what makes restarting one container at a time safe platform-wide.

Restart blast radius: scoring and training pause, failing **closed**.

This ``__init__`` exists so import-linter can see ``services.embedder`` as a module and hold it to
the same independence contract as every other service. It is deliberately empty otherwise: the
image copies ``gateway/`` to ``/app/gateway`` and runs ``gateway.app:app``, so inside the container
the package is ``gateway``, not ``services.embedder.gateway``. Both work because everything under
``gateway/`` uses relative imports — keep it that way.

Everything here runs from this directory: ``docker compose up -d --build``, ``pytest tests/``.
Design in ``docs/PLAN.md`` (the maintained copy), operations and the pre-deploy gates in
``README.md``.
"""
