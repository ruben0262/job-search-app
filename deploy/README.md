# deploy — compose stack, per-service Dockerfiles, operational scripts

## Current state

Running on the platform VPS (`169.58.16.241`, 23 GiB RAM, 8 vCPU, 382 GB free):

| Container | Stack | State |
|---|---|---|
| `job-search-platform-postgres-1` | `deploy/compose.yml` | ✅ healthy, schema applied |
| `job-search-app-tei-1` | `services/embedder/docker-compose.yml` | ✅ ~832 MiB / 2 GB budget |
| `job-search-app-embedder-gateway-1` | same | ✅ `verified: true` |

Two compose projects on purpose — the embedder keeps its own, see below. The harvester runs on a
15-minute systemd timer. `api`, `scorer`, `trainer`, `notifier` and `docgen` have code but no
container on the box yet; `capture` and `labeller` have both a Dockerfile and a compose service as
of 2026-07-28 and are waiting to be brought up.

**Not yet done:** off-box backups (deferred until the platform runs end to end) and the restore
drill. Both are required before the first invited user, not before then.

### Getting onto the box, and running a host-side script

```
ssh ruben@169.58.16.241
cd ~/job-search-app          # NOT /opt — the checkout is in the deploy user's home
```

**Host-side scripts need `DATABASE_URL` in the environment, and nothing sets it for you.** The
containers get it from `compose.yml`; a script run from the shell does not, and the failure is a
clear `RuntimeError` rather than a silent default — deliberately, because a default here would
silently point at the wrong database. Source `deploy/.env` and build it:

```bash
cd ~/job-search-app
set -a && . deploy/.env && set +a
export DATABASE_URL="postgresql+psycopg://jobsearch:${POSTGRES_PASSWORD}@127.0.0.1:5432/jobsearch"
```

`127.0.0.1` rather than `postgres`: the compose service name only resolves inside the network, and
the loopback mapping exists for exactly this.

**Credentials and passwords are prompted for, never passed as arguments** — a value on a command
line lands in shell history and in `ps` output for every user on the box. So there is no
`--password` flag, and there is no `--value` flag:

```bash
./.venv/bin/python deploy/scripts/set_password.py --email you@example.com
./.venv/bin/python deploy/scripts/set_credential.py --email you@example.com --kind telegram_bot
```

Both take `--stdin`-style piping if you need to script them; `set_password.py` reads the new
password twice from stdin, `set_credential.py` takes `--stdin`.

### Applied 2026-07-28

Migrations through `f1d84c2e9b07` are on the box, the `capture` and `labeller` workers are up with
zero restarts, and both accounts are fully configured:

| | Ruben | Andras |
|---|---|---|
| login | set | set |
| telegram | connected | connected |
| channels | 3 (`Hun_Job_Stream`, `LinkedIn_Job_Streams`, `Job_Streams`) | 1 (`Engineering Jobs`), all routes pointed at it |
| documents | 4, 28,949 chars | 5, 158,975 chars |

Andras's single channel is set as all three routes on purpose: `group_by_chat` keys on the resolved
chat id, so three routes to one chat produce **one** message rather than three.

The imported originals were deleted from the box after extraction. The platform stores document
*text* and never the file, and staging directory that outlived the import would quietly contradict
that.

```
deploy/
├── compose.yml            the platform stack — Postgres, harvester, capture, labeller
├── .env.example           copy to .env; compose refuses to start without it
├── docker/                Dockerfile.<service>, one per container
└── scripts/               operational one-shots run on the VPS
```

## Bring it up

```bash
cd deploy
cp .env.example .env
printf 'POSTGRES_PASSWORD=%s\n' "$(openssl rand -base64 32)" > .env
chmod 600 .env
docker compose up -d
docker compose ps                       # postgres must read (healthy)
```

Then, so `psql` and `pg_dump` stop prompting (libpq silently ignores a `.pgpass` whose permissions
are looser than 600, and gives no hint that is why):

```bash
echo "127.0.0.1:5432:jobsearch:jobsearch:$(grep POSTGRES_PASSWORD deploy/.env | cut -d= -f2-)" > ~/.pgpass
chmod 600 ~/.pgpass
```

Quickest shell into the database, no password at all — the official image trusts local socket
connections inside the container:

```bash
docker exec -it job-search-platform-postgres-1 psql -U jobsearch jobsearch
```

## Why the embedder is not in this file

`services/embedder/` keeps its **own** `docker-compose.yml` and is deployed today, serving the two
single-tenant stacks. That predates the platform and must not be disturbed by it — the embedder
relocation was a source-tree move, not a service migration (`CODE_STRUCTURE.md` §6). Folding it in
here to tidy the tree would be a service migration wearing a refactor's clothes.

## Scheduling the harvest

The shared cycle is a **one-shot command**, not a daemon with a sleep:

```bash
docker compose -f deploy/compose.yml run --rm --no-deps harvester   # one cycle
python -m services.harvester --dry-run                              # plan queries, fetch nothing
```

Driven by [`harvester.timer`](harvester.timer) every 15 minutes. Exit codes are the interface:

| Code | Meaning |
|---|---|
| 0 | A cycle ran — or another cycle held the lock and this tick skipped |
| 1 | The cycle raised |
| 2 | Configuration missing (no `EMBEDDER_URL`, no `DATABASE_URL`) |

**A skipped tick is not a failure.** With a 15-minute timer and an occasionally slow cycle,
treating overlap as an error would page somebody about the system working correctly. Overlap is
prevented by a Postgres advisory lock inside the cycle rather than by systemd, so it works the same
way when Phase 2 moves this into the queue.

⚠ **`Persistent=false` in the timer, deliberately.** Catching up missed runs after downtime would
fire several cycles back to back, each spending the full fan-out budget against sources that are
already grey-area. A missed harvest is simply a harvest that did not happen — the next one finds the
same jobs.

**Why universal rather than per-user:** there is no such thing as harvesting *for* one user when the
corpus is shared. One fetch serves everyone, so a per-user harvest interval would mean N cycles
fetching identical bytes. What users choose is how often they are **told**
(`users.notify_interval_min`, 15–1440) and how often their own paid LinkedIn tier runs
(`users.linkedin_interval_min`, floored at 60). Universal where the cost is shared, per-user where
it is personal.

## Rules

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
