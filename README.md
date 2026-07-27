# job-search-app

Umbrella repo for the job-search SaaS build. Each service is a self-contained subdirectory with
its own Dockerfile and dependencies, so they can be deployed and versioned independently while
sharing this repo's plans and conventions.

```
libs/         shared code — services import from here, and only here
services/     one folder per container; embedder is the only one built so far
web/          React + TypeScript SPA (Phase 3)
deploy/       compose stack, per-service Dockerfiles, VPS scripts
tests/        cross-service behaviour tests
docs/saas/    the platform plans the rest of the build follows
```

The dependency rule is one arrow, one direction: `services/*` imports `libs/*`, `libs` never
imports `services`, and **no service imports another service** — they talk over HTTP or the queue.
[`import-linter`](pyproject.toml) enforces it in CI, because a rule a linter checks survives a late
night and a rule in a document is a suggestion. Full layout and reasoning in
[`docs/saas/CODE_STRUCTURE.md`](docs/saas/CODE_STRUCTURE.md).

## Where the project is

**Phase 1 of 4** (shared core). [`CHANGELOG.md`](CHANGELOG.md) is the history — what changed and
why. Each folder's `README.md` carries its own **Current state**.

| | |
|---|---|
| Deployed | Postgres 17 + the platform embedder, both on the VPS |
| Built | [`libs/db`](libs/db/) — 8 tables, Alembic-managed, drift-checked · [`services/embedder`](services/embedder/) |
| Tests | 23 passed / 1 skipped (embedder) · 8 passed (schema) |
| Next | T2 dedup regression suite, then the `store.py` port — the critical path |

Roadmap and phasing: [`docs/saas/ROADMAP.md`](docs/saas/ROADMAP.md).

## Components

### [`services/embedder/`](services/embedder/) — shared embedding service ✅ deployed

One resident copy of BGE serves every tenant's scoring **and** training over HTTP, instead of each
stack loading its own ~1.5GB copy. HuggingFace TEI behind a thin FastAPI gateway that provides two
priority lanes (fast scoring vs bulk training), a backend-verified version contract, and bounded
queues.

Run it from its own directory — the compose project name is pinned to `job-search-app`, so it
matters neither where the repo lives nor where in the tree this directory sits. That pin is what
let it move from `embedder/` to `services/embedder/` without touching the running containers,
volumes, or network:

```bash
cd services/embedder
docker compose up -d --build
curl -s http://127.0.0.1:8080/health
```

Full design in [`services/embedder/docs/PLAN.md`](services/embedder/docs/PLAN.md), operations and
the pre-deploy gates in [`services/embedder/README.md`](services/embedder/README.md).

**Consumers:** `job-search` is migrated (scorer-service dropped from ~1.9GB to ~165MB, no model
resident). `andras-job-search` is next.

### The other services

`services/api`, `harvester`, `scorer`, `trainer`, `notifier`, `docgen`, `capture` and all of
`libs/` exist as empty packages. Each one's `__init__.py` records why it exists and what constraint
it carries, so the tree is navigable before it is populated. They are filled in during Phases 1–3
— see the roadmap.

## [`docs/saas/`](docs/saas/) — platform plans

**[`docs/saas/README.md`](docs/saas/README.md) is the index** — read order, every resolved
decision, and what still needs an answer from outside engineering. Start with
[PLATFORM_ARCHITECTURE.md](docs/saas/PLATFORM_ARCHITECTURE.md) (the spine) and
[ROADMAP.md](docs/saas/ROADMAP.md) (what is being built now). Deliberately not duplicated here —
one index, so it cannot go stale in two places.

⚠ These were copied from `job-search/docs/saas/`, which still has its own copies. Treat **this** as
the source of truth going forward and let the ones in `job-search` go stale, or they will drift.
`EMBEDDER_SHARING_PLAN.md` in particular is superseded by
[`services/embedder/docs/PLAN.md`](services/embedder/docs/PLAN.md), which is the one that has been
kept current against the running service.

## Development

```bash
pip install -r requirements-dev.txt
lint-imports                        # the boundary contracts — must stay green
pytest services/embedder/tests      # integration tier skips while EMBEDDER_URL is unset
```

## Conventions

The embedder establishes the pattern worth repeating:

- **A service owns its Dockerfile, `requirements.txt`, tests, and docs.** Per-service dependencies
  are why `docgen` can carry Chrome without making `api` slow to rebuild.
- **Pin `name:` in every compose file** so the project name doesn't follow the directory — moving a
  folder must never orphan a volume or rename a container.
- **Services verify their own claims.** `/health` reports what a live probe confirmed, not what the
  config file asserts; callers assert compatibility before trusting a result and fail closed.
- **Gates before cutover.** Anything a consumer depends on gets measured on the real box first —
  see [`services/embedder/tests/README.md`](services/embedder/tests/README.md) for how those are
  structured.
- **New file → a module docstring explaining why it exists**, not what it does. Non-trivial change
  → CHANGELOG entry. Major change → update the relevant doc in the same commit.
- **Generation prompts are SACRED.** No edit without explicit approval, A/B measured, changelogged.
