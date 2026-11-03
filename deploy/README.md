# deploy — the stack, its Dockerfiles, and what runs it

The server is a Proxmox VM, set up from [`SERVER.md`](SERVER.md). This file is how it runs from day to
day.

```
deploy/
├── SERVER.md              setting up the server: the VM, Docker, the settings, the tunnel
├── compose.yml            the stack: one container per part of the app
├── compose.sh             every compose command, with the settings from certificates/server.env
├── deploy.sh              pull, build, schema, restart, verify: everything, or the services named
├── server.env.example     copied to certificates/server.env on the server and filled in
├── install-units.sh       installs the timers below for this checkout and the account owning it
├── *.service, *.timer     the harvest and the health check every 15 minutes, the backup at 03:20
├── docker/                Dockerfile.<service>, one per container
└── scripts/               one-shots run on the server: backup, health, accounts, the company seed
```

**The settings live in `certificates/server.env`**, as a laptop's live in `certificates/dev.env`, never
in `deploy/`. `compose.sh` hands that file to compose, and the health and backup timers read it too.

## What runs where

Each part is its own container from its own Dockerfile, with its own memory ceiling, so one is rebuilt
or restarted while the rest keep running. The database is MongoDB Atlas, outside this stack.

| Service | Profile | What it does |
|---|---|---|
| `api` | `full` | the JSON API **and the site**, on one origin |
| `docgen` | `full` | the `docgen` lane: CVs and cover letters for download; **the only image with Chrome** |
| `notifier` | `full` | the `notify` lane: **messages real people** |
| `oauthbroker` | `full` | Claude sign-in sessions; exactly one replica |
| `cloudflared` | `full` | the only way in; no inbound port is open |
| `harvester` | `tools` | one harvest, every 15 minutes from `harvester.timer` |
| `seed-ats` | `tools` | the company boards from `scripts/ats_companies.txt`, run by `deploy.sh` when the list changes |

What each needs, measured, and the VM they need together: [`SERVER.md`](SERVER.md#what-it-needs).

## Deploying

```bash
deploy/deploy.sh               # everything
deploy/deploy.sh docgen        # one service: the others keep running
deploy/deploy.sh api notifier  # any set
```

In order, stopping at the first thing that fails:
- **pull** (`--ff-only`);
- **build** the services named, and `api`'s image, which the schema step runs in;
- **apply the schema** on Atlas (`python -m libs.db --prune`), before anything restarts;
- **restart** the long-running services named. A one-shot (`harvester`, `seed-ats`) is only built:
  its next run uses the new image;
- **bring up the tunnel** if it is down, without restarting it;
- **verify** that each container named runs this commit's `libs`, and that the tunnel is up.

Afterwards, when the company list changed, it seeds the new boards in the background
(`logs/seed_ats.log`).

`api` is the one restart a visitor notices: the site is away for the seconds the new container takes
to start. Everything else carries on.

## Day to day

```bash
deploy/compose.sh ps                                   # what is running
deploy/compose.sh logs -f --tail 100 api               # what one part is saying (files: logs/)
deploy/compose.sh stop notifier                        # pause alerts; `up -d notifier` resumes them
deploy/compose.sh run --rm --no-deps harvester         # one harvest now
deploy/compose.sh run --rm seed-ats --dry-run          # what the company seed would store
deploy/compose.sh run --rm seed-ats --ats workable     # one ATS again, e.g. after it refused a burst
curl -s https://mahoraga-app.rubenlekli.com/health     # {"status":"ok"}
```

The scripts in `scripts/` run on the VM itself, from `.venv`, with the settings loaded:

```bash
set -a && . certificates/server.env && set +a
.venv/bin/python deploy/scripts/check_health.py        # exit 0 / 3 warnings / 2 needs a person
.venv/bin/python deploy/scripts/show_accounts.py
```

## Moving the accounts off the old VPS (once)

The old VPS (`ruben@169.56.16.241`, unverified) still runs the pre-MongoDB stack, with the accounts
in Postgres. This copies the **accounts only** into Atlas (owner, 2026-10-30): people, logins,
settings, words, documents, profile files, credentials, browsers, and the operator's settings,
keywords and company boards. Postings are collected again within hours; applied and hidden marks, and
sessions, are not copied. Do it before the new server's first `deploy.sh`. On the **old** VPS:

1. **On the old code, back up and finish the Postgres schema.** The copy reads exactly one schema
   (the old checkout keeps its settings in `deploy/.env`):
   ```bash
   git fetch && git checkout model-on-postgres
   set -a && . deploy/.env && set +a
   export DATABASE_URL="postgresql+psycopg://jobsearch:${POSTGRES_PASSWORD}@127.0.0.1:5432/jobsearch"
   ./.venv/bin/python deploy/scripts/backup.py --out ~/backups      # pg_dump, verified
   ./.venv/bin/alembic upgrade head                                 # to 3c7d1e9a5b20
   ```
2. **Stop everything that writes**: `sudo systemctl stop harvester.timer backup.timer health.timer`,
   then `docker compose -f deploy/compose.yml --profile full --profile ml stop`. Postgres keeps
   running for the copy.
3. **Atlas**: allow the old VPS's public address under Network Access too, for the copy.
4. **The new code, and the copy.** A dry run first; it reads and converts everything and writes
   nothing:
   ```bash
   git checkout main
   ./.venv/bin/pip install -r requirements-dev.txt "psycopg[binary]"
   set -a && . deploy/.env && set +a                                 # POSTGRES_PASSWORD, SECRETS_KEK
   export MONGODB_URI='<the Atlas address>' MONGODB_DB=mahoraga
   export POSTGRES_URL="postgresql://jobsearch:${POSTGRES_PASSWORD}@127.0.0.1:5432/jobsearch"
   ./.venv/bin/python deploy/scripts/copy_accounts.py --dry-run
   ./.venv/bin/python deploy/scripts/copy_accounts.py
   ./.venv/bin/python deploy/scripts/show_accounts.py               # the same people, in MongoDB
   ```
5. **Carry the key across**: the old `deploy/.env`'s `SECRETS_KEK` goes into the new server's
   `certificates/server.env`, exactly. The copied credentials are sealed with it and open with nothing
   else.
6. **Start the new server** ([`SERVER.md`](SERVER.md), steps 7 and 6.4). Then stop the old stack,
   keeping Postgres's data: `docker compose -f deploy/compose.yml --profile full --profile ml down`.
   Remove the old VPS's address from Atlas.
7. **Tell the existing accounts** three things:
   - sign in once more (sessions were not copied);
   - connect their Telegram bot again in Settings if they had one (its token was not copied);
   - agree once more before an AI document: the disclosure now names Google and OpenAI.
8. **After two quiet weeks**, delete the old database on the old VPS:
   `docker rm job-search-platform-postgres-1 && docker volume rm job-search-platform_pgdata`.

**The way back**, while that volume exists: on the old VPS, `git checkout model-on-postgres`,
`docker start job-search-platform-postgres-1`, its own `deploy/deploy.sh`, and the old tunnel's
hostname back. Anything written to Atlas meanwhile stays there.

## Telegram, browser notifications and email

**Telegram** is each person's own bot (owner's decision, 2026-11-01), so there is nothing to set up
here. In Settings they follow three steps: make a bot with **@BotFather**, paste its token (checked
with Telegram's `getMe`), and press **Start** in it. The site finds their chat from that Start
(`getUpdates`), sends a welcome, and from then on the notifier sends through their bot with the token
decrypted under `SECRETS_KEK`. Jobs arrive as cards with two plain links, **Open job** and **Make my
CV**, so nothing in the chat needs a webhook.

**Browser notifications** need nothing beyond the `VAPID_*` keys in `server.env`. Generate that pair
once: a new one strands every browser that subscribed with the old.

**The daily email** of new jobs goes out at `email_digest_hour` (a platform setting, 8 by default) of
each person's own day, on the notifier's minute sweep, through the same SMTP mailbox and daily cap as
sign-up mail.

## Scheduling the harvest

The shared cycle is a **one-shot command**, not a daemon with a sleep, driven by
[`harvester.timer`](harvester.timer) every 15 minutes:

```bash
deploy/compose.sh run --rm --no-deps harvester   # one cycle, as the timer runs it
python -m services.harvester --dry-run           # plan the queries, fetch nothing
```

Exit codes are the interface:

| Code | Meaning |
|---|---|
| 0 | A cycle ran, or another cycle held the lock and this tick skipped |
| 1 | The cycle raised, a missing `MONGODB_URI` included |

**A skipped tick is not a failure.** With a 15-minute timer and an occasionally slow cycle,
treating overlap as an error would page somebody about the system working correctly. Overlap is
prevented by a lease in MongoDB inside the cycle (`harvest_lock`) rather than by systemd, and a cycle
that died lets go of it before the next tick.

⚠ **`Persistent=false` in the timer, deliberately.** Catching up missed runs after downtime would
fire several cycles back to back, each spending the full fan-out budget against sources that are
already grey-area. A missed harvest is simply a harvest that did not happen; the next one finds the
same jobs.

**One harvest for everyone:** the corpus is shared, so one fetch serves every account, and LinkedIn's
searches are pooled by query (2026-11-03). What a person chooses is how often they are told.

## Rules

- **Every compose command goes through `compose.sh`**, so every one reads `certificates/server.env`.
  A bare `docker compose` reads no settings and refuses to start.
- **Pin `name:`** in every compose file so the project name does not follow the directory. Moving a
  folder must never orphan a volume or rename a container.
- **Change one service with `deploy.sh <service>`.** It builds, migrates, restarts and verifies that
  one, and touches nothing else.
- **The schema is applied before anything restarts** (`python -m libs.db --prune`, in `deploy.sh`),
  and again, without pruning, as each service starts. Every step of it is idempotent, so services
  starting together cannot race into an outage (`CODE_STRUCTURE.md` §5).
- **Compose profiles**: the services that stay up are `full`; the one-shots are `tools`, so a bare
  `up` never starts a harvest that would then sit there.
- **Per-service dependencies stay per-service.** `docgen` is the only image with Chrome, and
  `harvester` the only one with jobspy. `api` and `notifier` stay small, because they are what gets
  restarted most.
