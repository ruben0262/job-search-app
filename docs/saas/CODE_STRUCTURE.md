# Code structure — layout, boundaries, and independent deploys

**Status:** design, 2026-07-26. The repo layout, module boundaries, and deployment mechanics that
make the container topology in `PLATFORM_ARCHITECTURE.md` §3 actually deliverable.

Two requirements drive everything here:

1. **Updating one part must not take the platform down.**
2. **A developer opening the repo should see logical correspondence** — not spaghetti.

---

## 1. The rule that prevents spaghetti

One dependency arrow, one direction:

```
   services/*  ──imports──▶  libs/*
        │                       │
        └── never ──────────────┘   libs never imports services
        └── never imports another service
```

**Services talk to each other over HTTP or the queue. Never by import.** The moment
`services/notifier` imports `services/scorer`, you have one program in eight folders — deployable
independently on paper and coupled in practice.

Enforce it mechanically, not by discipline. `import-linter` in CI with three contracts:

```ini
[importlinter:contract:layers]
name = services may not import each other
type = independence
modules = services.api, services.harvester, services.scorer, services.trainer,
          services.notifier, services.docgen, services.capture, services.embedder

[importlinter:contract:direction]
name = libs must not import services
type = forbidden
source_modules = libs
forbidden_modules = services
```

A rule a linter checks is a rule that survives a late night. A rule in a document is a suggestion.

## 2. Layout

```
job-search-app/
├── libs/                        # shared code — stable, no service dependencies
│   ├── contracts/               # Pydantic models shared BETWEEN services + JSON Schema export
│   ├── db/                      # SQLAlchemy models + Alembic migrations (the schema lives here)
│   ├── corpus/                  # dedup: canonical_url, content_key, link normalisation
│   ├── scoring/                 # features.py + rubric/profile rendering  ⚠ see §4
│   ├── prompts/                 # SACRED generation prompts + label/rubric templates
│   ├── secrets/                 # envelope encryption (KEK/DEK)
│   └── queue/                   # the four-lane job queue: claim, retry, dead-letter
│
├── services/                    # one folder per container, one Dockerfile each
│   ├── api/                     # FastAPI: auth, config, dashboard, admin console
│   ├── harvester/               # shared scrape + per-user LinkedIn loop
│   │   └── sources/             # ATS, boards, JobSpy adapters (lifted as-is)
│   ├── scorer/                  # multi-tenant model serving, LRU
│   ├── trainer/                 # queue worker, concurrency 1, CPU-fenced
│   ├── notifier/                # per-user Telegram delivery
│   ├── docgen/                  # CV/cover builds — Chrome lives only here
│   ├── capture/                 # Managed Agents profile capture
│   └── embedder/                # TEI + gateway — ALREADY BUILT, relocated (§6)
│
├── web/                         # React + TypeScript SPA
├── deploy/
│   ├── compose.yml
│   └── docker/Dockerfile.<service>
├── tests/                       # cross-service tests (T1, T5, T10)
└── docs/saas/
```

### Docs map to folders

Deliberate, so navigation is obvious:

| Document | Implements in |
|---|---|
| `HARVESTER_PLAN.md` | `services/harvester/` |
| `SCHEDULER_PLAN.md` | `libs/queue/` + the workers in `services/trainer`, `notifier`, `docgen` |
| `PROFILE_CAPTURE_PLAN.md` | `services/capture/` + `libs/scoring/` (rendering) |
| `TENANT_SAFETY.md` | `libs/secrets/`, `libs/db/`, `services/api/` |
| `COLD_START_PLAN.md` | `services/trainer/` + `services/capture/` |
| `OPERATIONS_PLAN.md` | `services/api/admin/` + metrics across all |
| `PLATFORM_ARCHITECTURE.md` §4 | `libs/db/` + `libs/corpus/` |

## 3. What goes where — the migration map

Concrete destinations for the reference instance's code, so the port is not a guessing game:

| Reference instance | Destination | Note |
|---|---|---|
| `pipeline/lib/store.py` | `libs/corpus/` | The big port. Dedup semantics are load-bearing |
| `pipeline/sources/*` | `services/harvester/sources/` | Lifted nearly as-is |
| `pipeline/scraper.py`, `processor.py` | `services/harvester/` | Split: scoring moves out of the harvest loop |
| `scorer/fetch_desc.py` | `services/harvester/fetch.py` | Keep `make_client()` as the only HTTP-client factory |
| **`scorer/features.py`** | **`libs/scoring/features.py`** | ⚠ **Must be one module.** See §4 |
| `scorer/train.py` | `services/trainer/` | Imports `libs/scoring` |
| `scorer/service.py` | `services/scorer/` | Imports `libs/scoring` |
| `scorer/label.py` | `services/trainer/labelling.py` | Queue-driven |
| `scorer/prompts/label.md` | `libs/prompts/label_template.md` | Becomes a template (`PROFILE_CAPTURE_PLAN.md` §6.1) |
| `pipeline/prompts/*.md` | `libs/prompts/` | **SACRED** — change control unchanged |
| `pipeline/cv_builder.py`, `cv_eval.py` | `services/docgen/` | Chrome dependency stays contained here |
| `pipeline/notifier.py`, `watcher/` | `services/notifier/` | |
| `pipeline/lib/{langfilter,workfilter,timefilter,salary}.py` | `libs/corpus/filters/` | Pure functions, shared |
| `pipeline/lib/config.py` | `libs/contracts/config.py` | Becomes the Pydantic schema the form renders from |
| `scorer/embedder_client.py` | `libs/contracts/embedder.py` | Already contract-shaped |

## 4. The one module that must not be duplicated

`libs/scoring/features.py` carries a constraint from its own docstring:

> *training and serving MUST build these identically, so both import THIS one function. Never inline
> a second copy.*

`services/trainer` and `services/scorer` are separate containers that **must agree byte-for-byte** on
feature construction. Two copies that drift do not crash — they silently score against different
semantics.

Three protections:

1. It lives in `libs/scoring/`, imported by both. Never copied.
2. `FEATURE_NAMES` order is platform-owned and global; only the per-user token lists vary
   (`PROFILE_CAPTURE_PLAN.md` §6.3).
3. The artifact records `profile_generation` and `feature_schema_version`; the scorer **fails closed**
   on mismatch rather than serving. Same discipline the embedder already applies to
   `(model, dim, version)`.

This is the one place where "independent services" yields to "these two must be in lockstep" — and
the version assert is what makes that safe.

## 5. Independent deploys — what that actually means

**Build coupling and deploy coupling are different problems.** Conflating them is why "microservices"
projects end up redeploying everything anyway.

- **Build:** a change in `libs/corpus` means every service importing it gets a new image. Fine —
  images are cheap and CI can rebuild all of them on every push at this scale. Don't over-engineer
  selective builds.
- **Deploy:** you still restart exactly one container.

```bash
git pull
docker compose build api
docker compose up -d --no-deps api     # --no-deps: touch nothing else
```

`--no-deps` is the flag that makes this true; without it Compose helpfully recreates linked services.

### What makes a one-service restart *safe*

Three things, all already established:

1. **Versioned contracts + fail closed.** Every service exposes `/health` with a version; callers
   assert compatibility and 503 rather than guessing (`PLATFORM_ARCHITECTURE.md` §5).
2. **Restart-safe workers.** A job is claimed transactionally; an expired claim returns to the queue.
   Killing a worker mid-job costs a retry, never a lost or duplicated user-visible action
   (`SCHEDULER_PLAN.md` §4).
3. **Migrations run separately from service start.** `alembic upgrade head` as its own one-shot step,
   never in a service entrypoint — otherwise two services racing to migrate on restart is a real
   outage. Additive-first schema changes (add column → deploy code → backfill → drop later) so old
   and new code can both run against the same database during a rollout.

### Per-service dependencies

Each service gets its own `requirements.txt` and Dockerfile — **already the pattern** in the reference
instance (four Dockerfiles). Keep it:

- `docgen` is the only image with Chrome.
- `trainer`/`scorer` are the only images with scikit-learn and numpy.
- `harvester` is the only image with jobspy.
- `api` and `notifier` stay small and fast to rebuild — which matters, because they are what you
  restart most.

The numpy split flagged in `SAAS_PLATFORM_PLAN.md` §5g is softer than recorded (the pipeline never
pinned numpy), but per-service requirements make it a non-issue either way.

### Compose profiles

Carry over the reference instance's split: always-on services start with a bare `up`; one-shot tools
sit behind `profiles: ["tools"]` so a no-arg `up` cannot accidentally launch a trainer beside the
scorer and load a second model into RAM. That exact mistake is why the profile split exists.

## 6. Relocating the embedder safely

`embedder/` is **built, tested, and deployed** — gateway, dispatcher, compose file, seven test
modules. It moves to `services/embedder/` so the tree has no exceptions (decided 2026-07-26).

**This is a source-tree move, not a service migration.** The running container, its image, and its URL
do not change; only the paths the build reads from. That is what makes it safe — but only if it is
done deliberately:

1. **Do it during Phase 1 step 0a**, while the repo layout is being established and nothing in the
   platform depends on it yet. Moving it later, with six services pointing at it, is strictly worse.
2. **Its own commit.** Nothing else in the same change, so a bisect lands on it cleanly.
3. **Tests green before and after.** The seven modules under `tests/` are the proof the move was
   inert; run them either side and compare.
4. **Update all four path surfaces together** — `docker-compose.yml` build context, the Dockerfile's
   `COPY` paths, test imports, and `scripts/`. A missed one fails at build, which is the good case;
   the bad case is a stale image that still works until it is rebuilt.
5. **`docs/PLAN.md` moves with it** and stays the maintained plan (the copy in `docs/saas/` is
   historical).

The deployed instance keeps serving throughout — redeploy only once tests are green on the new
layout.

**What does not change:** the gateway's two-lane dispatcher, its fail-closed version verification, and
the `(model, dim, version)` assert its callers depend on. Those are the parts other services rely on,
and this move must not touch them.

## 7. Conventions carried over

The reference instance's habits are good and should survive the port:

1. **New file → module docstring** explaining *why it exists*, not what it does. `dispatcher.py` in
   the embedder is the model: it documents the failed single-queue attempt so nobody re-tries it.
2. **Non-trivial change → CHANGELOG entry** (what / where / why).
3. **Major change → update the relevant doc in the same commit.**
4. **Generation prompts are SACRED** — no edit without explicit approval, A/B measured, changelogged.
   That rule now also covers `libs/prompts/label_template.md`, since editing it re-versions every
   user.
5. **Measure before optimising.** Every performance decision in these docs cites a measurement.

## 8. Frontend API client — generated, at Phase 3

**Decided 2026-07-26: generate the TypeScript client from the OpenAPI spec FastAPI already produces.**
Never hand-write it.

The point is that frontend and backend **cannot drift**: a changed response shape becomes a TypeScript
compile error rather than a runtime `undefined` in production. The same generator run gives the config
form its types, so `libs/contracts/config.py` stays the single definition behind both the server's
validation and the form's fields (`TENANT_SAFETY.md` §4 — "one Pydantic model, exported as JSON
Schema").

Wire it at **Phase 3**, when the API surface first stabilises. Generating against a still-churning API
means regenerating constantly for no benefit. Commit the generated client so CI can diff it — an
unexpected diff means someone changed a contract without meaning to.
