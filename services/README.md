# services — one folder per container

Split so any one can be rebuilt and restarted without taking the platform down. That is a hard
requirement, and it constrains the contracts between them.

**Services never import each other.** They talk over HTTP or the queue. Enforced by `import-linter`.

## Current state

| Service | Container | Restart blast radius |
|---|---|---|
| [`api/`](api/) | `api` | The site is away for the seconds it takes to start; everything else carries on |
| [`harvester/`](harvester/) | `harvester`, `seed-ats` (one-shots) | New jobs stop arriving until the next tick; existing feeds unaffected |
| [`docgen/`](docgen/) | `docgen` | CV builds queue up and are made when it is back |
| [`notifier/`](notifier/) | `notifier` | Alerts queue up, `next_run_at` re-armed |
| [`oauthbroker/`](oauthbroker/) | `oauthbroker` | A Claude sign-in under way has to start again |

What no longer runs (the embedder, the scorer, trainer, labeller, capture and setup agents) is in
[`retired/`](../retired/) or in git under the tag `model-on-postgres`.

## Deploying one service

```bash
deploy/deploy.sh docgen
```

It pulls, builds, applies the schema, restarts that one service and checks it runs the new code,
touching nothing else (`deploy/README.md`).

**Build coupling and deploy coupling are different problems.** A change in `libs/corpus` means every
service importing it gets a new image. Deploying is still exactly the containers named.

## What makes a one-service restart safe

Three things, all non-negotiable:

1. **Versioned contracts, fail closed.** Every service serves `/health` with a version; callers
   assert compatibility and 503 rather than guessing. A version mismatch is never a best-effort
   degraded answer.
2. **Restart-safe workers.** A job is claimed transactionally and an unfinished claim expires back
   into the queue, so killing a worker mid-job costs a retry — never a lost or duplicated
   user-visible action.
3. **Migrations run separately from service start.** Never in an entrypoint.

## Per-service dependencies stay per-service

Each service owns its `requirements.txt` and Dockerfile. `docgen` is the only image with Chrome and
`harvester` the only one with jobspy. `api` and `notifier` stay small, which matters, because they are
what gets restarted most.

## Compose profiles

The services that stay up are in the `full` profile; the one-shots (`harvester`, `seed-ats`) sit
behind `tools`, so a bare `up` never starts a harvest that would then sit there.
