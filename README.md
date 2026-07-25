# job-search-app

Umbrella repo for the job-search SaaS build. Each component is a self-contained subdirectory with
its own compose stack, so they can be deployed and versioned independently while sharing this
repo's plans and conventions.

```
embedder/     shared embedding service — the first component, in production
docs/saas/    the platform plans the rest of the build follows
```

## Components

### [`embedder/`](embedder/) — shared embedding service ✅ deployed

One resident copy of BGE serves every tenant's scoring **and** training over HTTP, instead of each
stack loading its own ~1.5GB copy. HuggingFace TEI behind a thin FastAPI gateway that provides two
priority lanes (fast scoring vs bulk training), a backend-verified version contract, and bounded
queues.

Run it from its own directory — the compose project name is pinned, so it does not matter where the
repo lives:

```bash
cd embedder
docker compose up -d --build
curl -s http://127.0.0.1:8080/health
```

Full design in [`embedder/docs/PLAN.md`](embedder/docs/PLAN.md), operations and the pre-deploy gates
in [`embedder/README.md`](embedder/README.md).

**Consumers:** `job-search` is migrated (scorer-service dropped from ~1.9GB to ~165MB, no model
resident). `andras-job-search` is next.

## [`docs/saas/`](docs/saas/) — platform plans

| Document | What it covers |
|---|---|
| [SAAS_PLATFORM_PLAN.md](docs/saas/SAAS_PLATFORM_PLAN.md) | The overall productisation path; the embedder is its first prerequisite |
| [CLAUDE_API_BACKEND_PLAN.md](docs/saas/CLAUDE_API_BACKEND_PLAN.md) | Backend/API design for LLM scoring |
| [POOLED_CLAUDE_ACCOUNT_PLAN.md](docs/saas/POOLED_CLAUDE_ACCOUNT_PLAN.md) | Shared Anthropic account/broker design |
| [MODEL_SELECTION_RESEARCH.md](docs/saas/MODEL_SELECTION_RESEARCH.md) | Model choice research |
| [EMBEDDER_SHARING_PLAN.md](docs/saas/EMBEDDER_SHARING_PLAN.md) | Historical: the plan that produced `embedder/` |

⚠ These were copied from `job-search/docs/saas/`, which still has its own copies. Treat **this** as
the source of truth going forward and let the ones in `job-search` go stale, or they will drift.
`EMBEDDER_SHARING_PLAN.md` in particular is superseded by
[`embedder/docs/PLAN.md`](embedder/docs/PLAN.md), which is the one that has been kept current
against the running service.

## Conventions for new components

The embedder establishes the pattern worth repeating:

- **A component owns its compose file, `.env.example`, tests, and docs.** No shared root compose.
- **Pin `name:` in every compose file** so the project name doesn't follow the directory — moving a
  folder must never orphan a volume or rename a container.
- **Services verify their own claims.** `/health` reports what a live probe confirmed, not what the
  config file asserts; callers assert compatibility before trusting a result and fail closed.
- **Gates before cutover.** Anything a consumer depends on gets measured on the real box first —
  see `embedder/tests/README.md` for how those are structured.
