# job-search-app

Umbrella repo for the job-search SaaS build. Each service is a self-contained subdirectory with
its own Dockerfile and dependencies, so they can be deployed and versioned independently while
sharing this repo's plans and conventions.

```
libs/         shared code — services import from here, and only here
services/     one folder per container
web/          React + TypeScript SPA
deploy/       compose stack, per-service Dockerfiles, VPS scripts
tests/        cross-service behaviour tests
docs/saas/    the platform plans the rest of the build follows
```

The dependency rule is one arrow, one direction: `services/*` imports `libs/*`, `libs` never
imports `services`, and **no service imports another service** — they talk over HTTP or the queue.
[`import-linter`](pyproject.toml) enforces it, because a rule a linter checks survives a late
night and a rule in a document is a suggestion. Full layout and reasoning in
[`docs/saas/CODE_STRUCTURE.md`](docs/saas/CODE_STRUCTURE.md).

## Where the project is

Runs on a Proxmox VM, set up from [`deploy/SERVER.md`](deploy/SERVER.md), with its data in MongoDB
Atlas. [`CHANGELOG.md`](CHANGELOG.md) is the history and [`docs/saas/ROADMAP.md`](docs/saas/ROADMAP.md)
the plan, including what is waiting on the owner.

## Components

Each part is its own container, so one can be rebuilt or restarted while the others keep running
([`deploy/compose.yml`](deploy/compose.yml)):

| Folder | Container | What it does |
|---|---|---|
| [`services/api/`](services/api/) and [`web/`](web/) | `api` | the site and its API, on one origin |
| [`services/harvester/`](services/harvester/) | `harvester`, `seed-ats` | finds postings every 15 minutes; finds the company boards to poll |
| [`services/docgen/`](services/docgen/) | `docgen` | makes CVs and cover letters; the only one with Chrome |
| [`services/notifier/`](services/notifier/) | `notifier` | Telegram, browser and email alerts |
| [`services/oauthbroker/`](services/oauthbroker/) | `oauthbroker` | Claude sign-in sessions |
| — | `cloudflared` | the Cloudflare Tunnel, the only way in |

Shared code is in [`libs/`](libs/). What the project no longer runs is in [`retired/`](retired/),
out of the way of what it does.

## [`docs/saas/`](docs/saas/) — platform plans

**[`docs/saas/README.md`](docs/saas/README.md) is the index** — read order, every resolved
decision, and what still needs an answer from outside engineering. Start with
[PLATFORM_ARCHITECTURE.md](docs/saas/PLATFORM_ARCHITECTURE.md) (the spine) and
[ROADMAP.md](docs/saas/ROADMAP.md) (what is being built now). Deliberately not duplicated here —
one index, so it cannot go stale in two places.

⚠ These were copied from `job-search/docs/saas/`, which still has its own copies. Treat **this** as
the source of truth going forward and let the ones in `job-search` go stale, or they will drift.

## Run it on your computer

```bash
scripts/dev.sh
```

Then open **http://localhost:5180**: its own port, because another project's site on Vite's usual
5173 answered there instead. One terminal runs everything: the database, the API, the CV maker, a job
fetch every 15 minutes, and the site. Ctrl-C stops it all. Running it again restarts it, whichever
terminal the running copy is in.

**Email.** Put the Zoho mailbox's password in `certificates/smtp.env` (made for you, password left
blank) and sign-up emails are really sent, from the same address as the live site. Until then none
are, and the sign-up link prints in that terminal instead.

Once, before the first run: `.venv/bin/pip install -r requirements-dev.txt -r
services/harvester/requirements.txt` (the second brings the LinkedIn scraper),
`npm --prefix web install`, and MongoDB 8.0 unpacked into `~/.local/opt/mongodb`. The script makes
`certificates/dev.env` (keys for your computer only) the first time.

The checks to run before a push are in [`CLAUDE.md`](CLAUDE.md) §7.

## Conventions

The patterns worth repeating, first set by the embedder (now in `retired/`):

- **A service owns its Dockerfile, `requirements.txt`, tests, and docs.** Per-service dependencies
  are why `docgen` can carry Chrome without making `api` slow to rebuild.
- **Pin `name:` in every compose file** so the project name doesn't follow the directory — moving a
  folder must never orphan a volume or rename a container.
- **Services verify their own claims.** `/health` reports what a live probe confirmed, not what the
  config file asserts; callers assert compatibility before trusting a result and fail closed.
- **Gates before cutover.** Anything a consumer depends on gets measured on the real box first
  (CLAUDE.md §7 lists the gates).
- **New file → a module docstring explaining why it exists**, not what it does. Non-trivial change
  → CHANGELOG entry. Major change → update the relevant doc in the same commit.
- **Generation prompts are SACRED.** No edit without explicit approval, A/B measured, changelogged.
