# deploy — compose stack, per-service Dockerfiles, operational scripts

## Current state

### Live, 2026-07-28

**`https://mahoraga-app.rubenlekli.com`** — the API, through the Cloudflare Tunnel.

The security property that buys is worth checking rather than assuming, so it was: the only port
listening on a public interface is **22**. The API's 8000 is container-internal with no host
binding, Postgres is loopback-only, and cloudflared makes an outbound connection that traffic
arrives back down. There is nothing on this box to portscan.

| Container | Profile | State |
|---|---|---|
| `postgres` | default | healthy |
| `capture`, `labeller` | default | running, 0 restarts |
| `scorer` | `full` | running — backlog drained, 4,314 rows scored per account |
| `api`, `cloudflared` | `full` | running — health 200 from the public internet |
| `notifier`, `trainer` | `full` | **built, not started** — starting the notifier messages real people |
| `docgen` | `full` | **built, not deployed** — the image has never been built on the box |

```bash
docker compose -f deploy/compose.yml --profile full up -d notifier   # when you want delivery on
```

### Timers

| Unit | State | Why |
|---|---|---|
| `harvester.timer` | enabled | 15-minute shared harvest |
| `backup.timer` | install below | nightly dump at 03:20 |
| `health.timer` | **installed, deliberately disabled** | see below |

```bash
sudo cp deploy/{health,backup}.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now backup.timer
systemctl list-timers backup.timer
```

**`health.timer` was held back** because its silent-feed alert was a true positive while nothing
delivered. That alert counted matches the scorer had written, and went with the scorer (ROADMAP
Phase 8), so the reason no longer holds. Enable it in the same change that starts delivery.

### Accepted risk: backups are on the box, 2026-07-28

The dumps live in `~/backups`, on the same disk as the database they protect. That covers the
likely failure — a bad migration, a dropped table, an operator mistake — and does **not** cover disk
or host loss. Off-box storage is deferred until there is somewhere to put it; recorded here because
an accepted risk that is not written down is one somebody later mistakes for an oversight.

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
├── compose.yml            the platform stack
├── compose.embedder.yml   the embedder — its own project, see "Running the platform"
├── .env.example           copy to .env; compose refuses to start without it
├── docker/                Dockerfile.<service>, one per container
└── scripts/               operational one-shots run on the VPS
```

## Switching the live box to MongoDB — once (Phases 8 and 9 together)

The box still runs the pre-Phase-8 services on Postgres. This moves it to the current code on MongoDB
Atlas, copying the **accounts only** (owner, 2026-10-30): people, logins, settings, words, documents,
profile files, credentials, browsers, plus the operator's settings, keywords and company boards.
Postings are collected again within hours; applied and hidden marks, and sessions, are not copied.
In this order:

1. **On the old code, back up and finish the Postgres schema.** The copy reads exactly one schema:
   ```bash
   git fetch && git checkout model-on-postgres
   set -a && . deploy/.env && set +a
   export DATABASE_URL="postgresql+psycopg://jobsearch:${POSTGRES_PASSWORD}@127.0.0.1:5432/jobsearch"
   ./.venv/bin/python deploy/scripts/backup.py --out ~/backups      # pg_dump, verified
   ./.venv/bin/alembic upgrade head                                 # to 3c7d1e9a5b20
   ```
2. **Stop everything that writes**: `sudo systemctl stop harvester.timer backup.timer health.timer`,
   then `docker compose -f deploy/compose.yml --profile full --profile ml stop`. Postgres keeps running
   for the copy.
3. **Atlas**: allow this box's public address under Network Access (and nothing wider), then add
   `MONGODB_URI` and `MONGODB_DB` to `deploy/.env` (`.env.example` says where to find them). Also the
   Phase 8 settings: the `VAPID_*` trio, `PUBLIC_URL`, and — for the AI allowance and the help chat — `PLATFORM_AI_PROVIDER`
   and `PLATFORM_AI_KEY` (a **paid** key; leave both empty and only people's own keys write AI documents).
4. **The new code, and the copy** — a dry run first; it reads and converts everything, writes nothing:
   ```bash
   git checkout main
   ./.venv/bin/pip install -r requirements-dev.txt "psycopg[binary]"
   set -a && . deploy/.env && set +a
   export POSTGRES_URL="postgresql://jobsearch:${POSTGRES_PASSWORD}@127.0.0.1:5432/jobsearch"
   ./.venv/bin/python deploy/scripts/copy_accounts.py --dry-run
   ./.venv/bin/python deploy/scripts/copy_accounts.py
   ./.venv/bin/python deploy/scripts/show_accounts.py               # the same people, in MongoDB
   ```
5. **Retire the old containers, keep Postgres's data**: `docker rm -f` the five switched-off services
   (`job-search-platform-{capture,setupagent,labeller,scorer,trainer}-1`), then
   `docker stop job-search-platform-postgres-1`. Its volume stays for two weeks as the way back.
6. **Deploy**: `deploy/deploy.sh` — it builds, applies the schema (`python -m libs.db --prune`) and
   recreates. Then `sudo deploy/install-units.sh` (the backup and health units changed) and start the
   timers again.
7. **Check**: `/health`, `deploy/scripts/check_health.py`.
8. **Tell the existing accounts** three things: sign in once more (sessions were not copied); connect
   their Telegram bot again in Settings if they had one (its token was not copied); and agree once more before an AI
   document — the disclosure now names Google and OpenAI.
9. **Afterwards**: change the Atlas database user's password (it has been shared in a chat) and update
   `deploy/.env`. After two quiet weeks, delete the old database:
   `docker rm job-search-platform-postgres-1 && docker volume rm job-search-platform_pgdata`, and drop
   `POSTGRES_PASSWORD` from `deploy/.env`.

**The way back**, while the volume exists: `git checkout model-on-postgres`, `docker start
job-search-platform-postgres-1`, `deploy/deploy.sh`. Anything written to MongoDB meanwhile stays there.

## Running the platform

Everything is in this directory now, including the embedder's compose file and every Dockerfile.
Two compose projects, because the embedder predates the platform and is shared with the two
single-tenant stacks — disturbing it to tidy the tree would be a service migration dressed up as a
refactor.

### 1. Configuration

```bash
cd ~/job-search-app
cp deploy/.env.example deploy/.env      # first time only
chmod 600 deploy/.env
```

Fill in `MONGODB_URI`, `SECRETS_KEK`, `ACCESS_TOKEN_SECRET`, `TUNNEL_TOKEN` and the `SMTP_*`
block. **`SECRETS_KEK` is the one that cannot be regenerated** — every stored credential is sealed
with it, and losing it means every user re-enters every secret.

### 2. The embedder — not needed

Nothing live embeds any more (ROADMAP Phase 8): jobs are matched on their words, and the harvester
neither calls the embedder nor joins its network. It stays deployable for the switched-off model:

```bash
docker compose -f deploy/compose.embedder.yml up -d --build
curl -s localhost:8080/health          # expect verified: true
```

### 3. The platform

```bash
# The database is MongoDB Atlas, off this box. The schema — safe to run any time, and every service
# applies it again as it starts. Needs MONGODB_URI in the environment (see below).
./.venv/bin/python -m libs.db

# Everything else: the API, the site, the tunnel, CV building and delivery.
docker compose -f deploy/compose.yml --profile full up -d --build
```

**The `full` profile is deliberate.** `notifier` sends messages to real people, so bringing the
platform up must not start delivery as a side effect of typing `up`. Start it on its own when you
mean to:

```bash
docker compose -f deploy/compose.yml --profile full up -d notifier
```

### 4. Check it

```bash
curl -s https://mahoraga-app.rubenlekli.com/health          # {"status":"ok"}
./.venv/bin/python deploy/scripts/check_health.py           # exit 0 / 1 / 2
./.venv/bin/python deploy/scripts/show_accounts.py
```

### Host-side scripts need `MONGODB_URI`

Nothing sets it for you. The containers get it from `compose.yml`; a shell does not, and the failure
is a clear `RuntimeError` rather than a silent default pointing at the wrong database.

```bash
cd ~/job-search-app
set -a && . deploy/.env && set +a
```

### What runs where

| Service | Profile | What it does |
|---|---|---|
| `postgres` | default | everything: corpus, per-user tail, credentials, queue |
| `capture` | `ml` | switched off — `process` lane, documents to a draft profile |
| `setupagent` | `ml` | switched off — `setup` lane, the setup chat's agents |
| `labeller` | `ml` | switched off — `label` lane, relabel and cold start |
| `scorer` | `ml` | switched off — `score` lane, corpus rows to per-user scores |
| `trainer` | `ml` | switched off — `train` lane, fits a user's model |
| `notifier` | `full` | `notify` lane — **messages real people** |
| `docgen` | `full` | `docgen` lane — builds CVs and cover letters for download; **the only image with Chrome** |
| `api` | `full` | the JSON API **and the site**, on one origin |
| `cloudflared` | `full` | the only way in; no inbound port is open |
| `harvester` | `tools` | one cycle, invoked by a systemd timer |

## Telegram, browser notifications and email

**Telegram** is each person's own bot (owner's decision, 2026-11-01), so there is nothing to set up
here. In Settings they follow three steps: make a bot with **@BotFather**, paste its token (checked
with Telegram's `getMe`), and press **Start** in it. The site finds their chat from that Start
(`getUpdates`), sends a welcome, and from then on the notifier sends through their bot with the token
decrypted under `SECRETS_KEK`. Jobs arrive as cards with two plain links, **Open job** and **Make my
CV**, so nothing in the chat needs a webhook.

**Browser notifications** need nothing beyond the `VAPID_*` keys in `.env.example`. Generate that pair
once: a new one strands every browser that subscribed with the old.

**The daily email** of new jobs goes out at `email_digest_hour` (a platform setting, 8 by default) of
each person's own day, on the notifier's minute sweep, through the same SMTP mailbox and daily cap as
sign-up mail.

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
| 1 | The cycle raised — a missing `MONGODB_URI` included |

There is no 2 any more: it meant "no `EMBEDDER_URL`", and nothing embeds (ROADMAP Phase 8).

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
- **The schema is applied before anything restarts** (`python -m libs.db --prune`, in `deploy.sh`), and
  again, without pruning, as each service starts. Every step of it is idempotent, so services starting
  together cannot race into an outage the way two Alembic runs could (`CODE_STRUCTURE.md` §5).
- **Compose profiles**, carried over from the reference instance: always-on services start with a
  bare `up`; one-shot tools sit behind `profiles: ["tools"]`. That split exists because a no-arg
  `up` once launched a trainer beside the scorer and loaded a second model into RAM. The
  switched-off model (CLAUDE.md §3) sits behind `profiles: ["ml"]`, which nothing starts.
- **Per-service dependencies stay per-service.** `docgen` is the only image with Chrome;
  `trainer`/`scorer` the only ones with scikit-learn and numpy; `harvester` the only one with
  jobspy. `api` and `notifier` stay small, because they are what gets restarted most.
