# Changelog

What changed, where, and **why** — the last one is the point. A diff already records what changed;
this file exists so that six months from now the reasoning behind a decision is recoverable without
archaeology.

Conventions (carried from the reference instance, `docs/saas/CODE_STRUCTURE.md` §7):

- **Non-trivial change → an entry here.** Formatting and typo fixes do not need one.
- **Major change → update the relevant doc in the same commit**, and say so in the entry.
- Newest first. Dates are the day the work landed.
- Per-folder state lives in that folder's `README.md` under **Current state**; this file is history,
  those are position. Deliberately not duplicated — two copies of the same fact drift.

---

## 2026-07-27

### Embedder deployed on the platform box

`services/embedder` now runs on `169.58.16.241` as the **platform's own instance**. The legacy
deployment serving `job-search` and `andras-job-search` lives on a different machine and was not
touched — decision D6 is parallel copies, decommission nothing until the platform provably matches.

Gate results, run in order per `services/embedder/README.md`:

| Gate | Result |
|---|---|
| 1 — backend-verified health | ✅ `verified: true` in ~45 s, `model=BAAI/bge-base-en-v1.5 dim=768` |
| 2 — full suite against the live stack | ✅ **23 passed, 1 skipped** (up from 15/9 with no stack) |
| 5 — priority lane under a training flood | ✅ included in gate 2 |
| 4 — peak RSS vs the 2 GB budget | ✅ 832 MiB resident after a 2,000-text cold-start bulk |

The single skip is `test_parity` (TEI vectors vs in-process `fastembed`). **That gate is moot for the
platform** and was skipped deliberately: parity exists to decide *match-or-retrain* for classifiers
that already exist, and the platform has none — every user's model will be trained on TEI vectors
from day one. It still matters for the legacy box, which is why the test stays in the suite.

Tests were run in a throwaway `python:3.12-slim` container attached to `embedder-net` rather than a
host venv. That needs no `sudo` (`python3-venv` is not installed) and, more usefully, exercises the
path consumers actually use — the gateway by service name, not the loopback host port.

### Postgres schema — eight tables, Alembic-managed (Phase 1 step 1)

`libs/db`. Applied to the live database and verified: re-running autogenerate produces **0
operations**, so models and database agree exactly.

Three decisions that are not transcriptions of the design docs:

- **Added `jobs.finished_at`.** `SCHEDULER_PLAN.md` §3's DDL omits it, but §5's fairness ordering
  selects `MAX(finished_at)` per user per lane. Round-robin does not work without it.
- **`job_id` foreign keys are `RESTRICT`, not `CASCADE`.** Written as CASCADE first, then corrected:
  cascading there would let a corpus retention sweep silently delete `applied_at`, which §4 calls
  the record a user would be angriest to lose. `user_id` still cascades, so account deletion stays
  one statement.
- **Config split by access pattern.** The four fields the 60-second dispatcher reads (`active`,
  `next_run_at`, `notify_interval_min`, `timezone`) are indexed columns on `users`; everything else
  the config form collects is JSONB on `user_config`.

Closes **G5** (Alembic from the first table, no hand-applied DDL ever) and **G7** (every timestamp is
`TIMESTAMPTZ`, so per-day counters can reset at the user's local midnight rather than UTC's).

`tests/test_schema.py` is the seed of **T1** — written now because the roadmap puts the isolation
test at the moment the first per-user table exists, not once there is a repository layer to test
through. CI runs it against a real Postgres 17 service, plus a drift check that fails if a model
changed without a migration.

### Postgres deployed

`deploy/compose.yml`. Loopback-only port, `${POSTGRES_PASSWORD:?}` so the stack refuses to start
without a password rather than defaulting to a guessable one, `mem_limit: 4g` because the box runs
no swap, `shm_size: 1g` because the 64 MB default makes parallel query workers fail with errors that
read like disk problems.

### Legal review cleared

GDPR posture, scraping ToS, and Anthropic's terms on subscription credentials in a hosted service
all confirmed cleared. The **implementation** obligations they imply are scheduled into Phase 3 —
see `docs/saas/ROADMAP.md`. A review passing and the obligations being built are different things.

## 2026-07-26

### Step 0b — capacity measured, D4 confirmed

23 GiB RAM with 22 GiB available, 8 vCPU, 382 GB free disk, ~337 MiB resident. Roughly 3× headroom
against the projection. Recorded in `PLATFORM_ARCHITECTURE.md` §11 along with the caveat that
matters more than the verdict: **neither single-tenant stack was on the box**, so the ~3.8 GB that
made the D6 arithmetic tight has not been paid yet.

Surfaced three follow-ups: the embedder needed standing up here (done 2026-07-27), the box has **no
swap**, and Postgres was not installed (done 2026-07-27).

### `embedder/` → `services/embedder/`

Its own commit, so a bisect lands on it cleanly. **15 passed / 9 skipped before and after,
identical** — the proof the move was inert.

Nothing relative needed editing: the compose build context, the Dockerfile `COPY` paths, and
`conftest.py`'s `sys.path` insert are all computed relative to files that moved together, and the
compose project name is pinned. Only things hardcoding a path from the repo root changed.
`CODE_STRUCTURE.md` §6 now records which surfaces those actually were, because the pre-move guess
was wrong in the direction that matters.

### Step 0a — repo layout and boundary enforcement

`libs/` and `services/` per `CODE_STRUCTURE.md` §2, as empty packages whose `__init__` docstrings
record why each exists. `import-linter` wired into CI on the first commit: **services may not import
each other**, **libs may not import services**. Every service is named in the contract from the
start, including still-empty ones, so a new service is covered the moment it has code rather than
whenever someone remembers to add it.

Boundaries are trivial to establish now and painful to retrofit once eight services import each
other. A rule a linter checks survives a late night; a rule in a document is a suggestion.

### Platform design

`docs/saas/` — twelve documents settling the shared/per-user split, container topology, storage
sizing, scheduler, tenant safety, onboarding, and operations. Start at
`docs/saas/PLATFORM_ARCHITECTURE.md`.
