# deploy — compose stack, per-service Dockerfiles, operational scripts

```
deploy/
├── compose.yml            the platform stack (not yet written — Phase 1)
├── docker/                Dockerfile.<service>, one per container
└── scripts/               operational one-shots run on the VPS
```

## Why this is not one compose file yet

`services/embedder/` keeps its **own** `docker-compose.yml` and is deployed today, serving the two
single-tenant stacks. That predates the platform and must not be disturbed by it — the embedder
relocation was a source-tree move, not a service migration (`CODE_STRUCTURE.md` §6). The platform's
`compose.yml` lands in Phase 1 alongside the first services that need it.

## Rules for when it does exist

- **Pin `name:`** in every compose file so the project name does not follow the directory. Moving
  a folder must never orphan a volume or rename a container. (`README.md` — conventions.)
- **Deploy one service at a time:**
  ```bash
  git pull
  docker compose build api
  docker compose up -d --no-deps api     # --no-deps: touch nothing else
  ```
  Without `--no-deps`, Compose helpfully recreates linked services and the blast radius is the
  whole stack again.
- **Migrations are their own one-shot step** (`alembic upgrade head`), never a service entrypoint.
  Two services racing to migrate on restart is a real outage (`CODE_STRUCTURE.md` §5).
- **Compose profiles**, carried over from the reference instance: always-on services start with a
  bare `up`; one-shot tools sit behind `profiles: ["tools"]`. That split exists because a no-arg
  `up` once launched a trainer beside the scorer and loaded a second model into RAM.
- **Per-service dependencies stay per-service.** `docgen` is the only image with Chrome;
  `trainer`/`scorer` the only ones with scikit-learn and numpy; `harvester` the only one with
  jobspy. `api` and `notifier` stay small, because they are what gets restarted most.
