# services — one folder per container

Split so any one can be rebuilt and restarted without taking the platform down. That is a hard
requirement, and it constrains the contracts between them.

**Services never import each other.** They talk over HTTP or the queue. Enforced by `import-linter`.

## Current state

| Service | State | Restart blast radius |
|---|---|---|
| [`embedder/`](embedder/) | ✅ **built + deployed** | Scoring and training pause, failing **closed** |
| [`harvester/`](harvester/) | empty | New jobs stop arriving; existing feeds unaffected |
| [`api/`](api/) | empty | Users cannot log in or edit config; pipeline keeps running |
| [`scorer/`](scorer/) | empty | New jobs queue unscored, drain on restart |
| [`trainer/`](trainer/) | empty | Retrains queue up |
| [`notifier/`](notifier/) | empty | Deliveries queue up, `next_run_at` re-armed |
| [`docgen/`](docgen/) | empty | CV builds queue up |
| [`capture/`](capture/) | empty | Onboarding capture unavailable; manual entry is the fallback |

Each empty package's `__init__.py` records why it exists and what constraint it carries. They fill in
during Phases 1–3 — `harvester` is next (Phase 1 step 4).

## Deploying one service

```bash
git pull
docker compose build api
docker compose up -d --no-deps api     # --no-deps: touch nothing else
```

`--no-deps` is the flag that makes this true. Without it Compose helpfully recreates linked services
and the blast radius is the whole stack again.

**Build coupling and deploy coupling are different problems.** A change in `libs/corpus` means every
service importing it gets a new image — fine, images are cheap and CI rebuilds all of them. Deploying
is still exactly one container.

## What makes a one-service restart safe

Three things, all non-negotiable:

1. **Versioned contracts, fail closed.** Every service serves `/health` with a version; callers
   assert compatibility and 503 rather than guessing. A version mismatch is never a best-effort
   degraded answer. The embedder already does this and is the model to copy.
2. **Restart-safe workers.** A job is claimed transactionally and an unfinished claim expires back
   into the queue, so killing a worker mid-job costs a retry — never a lost or duplicated
   user-visible action.
3. **Migrations run separately from service start.** Never in an entrypoint.

## Per-service dependencies stay per-service

Each service owns its `requirements.txt` and Dockerfile. `docgen` is the only image with Chrome;
`trainer`/`scorer` the only ones with scikit-learn; `harvester` the only one with jobspy. `api` and
`notifier` stay small — which matters, because they are what gets restarted most.

## Compose profiles

Always-on services start with a bare `up`; one-shot tools sit behind `profiles: ["tools"]`. That
split exists because a no-arg `up` once launched a trainer beside the scorer and loaded a second
model into RAM.
