# libs/db — schema and migrations

The schema lives here, in one place. Postgres is the only datastore (decision D1): shared corpus,
per-user tail, credentials, and the four-lane job queue in one database.

## Current state

✅ **Built and applied.** Eleven tables, three Alembic revisions, live on the platform VPS. Verified by
re-running autogenerate against the migrated database and getting **0 operations** — models and
database agree exactly.

| Table | Scope | What it holds |
|---|---|---|
| `users` | per-user | Account, lifecycle status, timezone, and the fields the 60-second dispatcher reads |
| `user_config` | per-user | The config form's contents as JSONB, with a `schema_version` |
| `user_secrets` | per-user | Claude / Telegram / proxy credentials, envelope-encrypted, write-only |
| `corpus` | **shared** | Public job postings, dedup keys, embeddings. The only non-per-user table |
| `user_job_state` | per-user | Score, seen, notified, **applied** |
| `user_labels` | per-user | Training labels, referencing `job_id` — never copying job text |
| `models` | per-user | Trained artifact metadata; at most one active per user |
| `jobs` | per-user | The four-lane work queue |
| `ats_tokens` | **shared** | Harvested company board tokens; dead ones retained, not deleted |
| `ats_etags` | **shared** | Conditional-poll validators. Row-per-board, replacing the reference file lock |

## The rules this schema encodes

**Account deletion is one statement.** Every per-user table carries
`user_id UUID REFERENCES users(id) ON DELETE CASCADE`, so `DELETE FROM users WHERE id = $1` is
complete by construction rather than by remembering to write cleanup code.

**But `job_id` foreign keys are `RESTRICT`, not `CASCADE`.** Deleting an *account* must take its
rows; deleting a *job* must not. `user_job_state.applied_at` is the durable "what I applied to"
record, and a corpus retention sweep that quietly removed it would be exactly the data loss
`PLATFORM_ARCHITECTURE.md` §4 warns about. Retention nulls `description`/`embedding` and keeps the
row; acted-on rows are also `pinned`. `RESTRICT` is the backstop for a `DELETE` that should never
have been written.

**No per-user row contains job text.** This is what keeps the storage projection true — 0.87 GB
steady state against 44 GB for per-user copies at 50 users.

**The corpus vector is keyed on `text_sha256`, not on the row id.** Descriptions get re-fetched and
mutated on live postings, so an id-keyed vector silently goes stale against text it no longer
represents — and a stale vector does not error, it just scores wrong.

**Every timestamp is `TIMESTAMPTZ`** (G7), so per-day quota counters reset at the user's local
midnight rather than UTC's. Asserted by a test against the live schema, not just the models.

## Migrations

```bash
export DATABASE_URL='postgresql+psycopg://jobsearch:<pw>@127.0.0.1:5432/jobsearch'
alembic upgrade head                              # apply
alembic revision --autogenerate -m "what changed" # after editing a model
alembic downgrade -1                              # step back
```

⚠ **`alembic upgrade head` runs as its own one-shot step, never from a service entrypoint.** Two
services racing to migrate on restart is a real outage. Schema changes are **additive-first** — add
column, deploy code, backfill, drop later — so old and new code can both run against the same
database during a rollout.

⚠ **A model not imported by [`models/__init__.py`](models/__init__.py) is invisible to autogenerate.**
It produces no migration, no table, and no error. That is this setup's one silent failure mode.

## Tests

[`tests/test_schema.py`](../../tests/test_schema.py) — the seed of T1. Proves cascade completeness,
corpus survival, `RESTRICT`, queue duplicate-collapse, interval bounds, one-active-model, and
timezone-awareness. Needs a live Postgres:

```bash
DATABASE_URL=... pytest tests/test_schema.py -v      # skips without it
```

CI runs them against a real Postgres 17 service, plus a drift check that fails if a model changed
without a migration.

## Not yet built

The repository layer. It takes `user_id` as a **required** argument, never an optional filter a
caller may forget — a query that omits the tenant should fail to typecheck rather than quietly
return another user's rows. The query-level half of T1 arrives with it.
