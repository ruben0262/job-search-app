# Scheduler — one work queue, four lanes

## Implementation status — audited 2026-07-31

**Fully implemented, and the platform has gone past it.** Every mechanism in this document is in
`libs/queue`, and nothing here has needed revising — which is unusual enough among these plans to
be worth saying.

- **§3 `FOR UPDATE SKIP LOCKED`** is the claim protocol, exactly as specified.
- **§3's `dedup_key`** is a partial unique index over `(lane, user_id, dedup_key)` where the state
  is `ready` or `claimed` — a *constraint*, not an application check, because five labels arriving
  at once must produce one retrain even when they race.
- **§4 restart safety** — `CLAIM_TIMEOUT_MIN` per lane, swept on every worker loop. A test now
  pins that the lane list and the timeout map agree by construction, after `setup` was added
  without one and crash-looped its worker on the first sweep rather than the first job.
- **§5 fairness** — `finished_at` per user per lane, which is why the column exists.
- **§7 trigger policy** — `trainer.should_retrain`, the other half of the dedup key.

**Where it has grown:** four lanes were designed (`notify train docgen label`); there are seven.
`process` and `score` were added in Phase 2–4, and `setup` on 2026-07-30 — a chat turn takes
minutes on a frontier model, so running it inside the HTTP request meant switching tabs killed it.
Each addition needed a compose service, a claim timeout and a migration to the lane constraint, and
nothing else: the design absorbed them, which is the strongest thing that can be said about it.


**Status:** design, 2026-07-26. Covers training, notification delivery, document generation, and
backfill labelling. **These are the same problem** — bounded work items, per-user fairness, must not
starve each other, must survive a container restart — so they get one mechanism, not four.

---

## 1. Why one mechanism

The brief treats training scheduling and notification scheduling as separate hard problems. They
differ only in trigger and cost profile:

| Lane | Trigger | Bound by | Concurrency | Latency tolerance |
|---|---|---|---|---|
| `notify` | time (`next_run_at` due) | network (Telegram) | 8 | seconds — a late feed is a bad product |
| `train` | event (user marked dirty) + debounce | **CPU + RAM** | **1** | minutes — nobody is watching |
| `docgen` | user taps Build CV | Claude API + Chrome (~90–130 s) | 3 | ~2 min, user is waiting |
| `label` | cold start / backlog | Claude API (batchable) | 2 | hours |

Same queue, same claim protocol, same retry and dead-letter behaviour. Different worker pools and
different concurrency caps. This is exactly the shape the embedder gateway already proved: **separate
worker pools per lane, not one pool with priorities.**

That lesson is worth restating because it was learned the expensive way. The embedder first shipped a
single priority queue; score latency still collapsed from 0.08 s to 8–15 s under a bulk flood, because
priority decides who is picked up *next* and cannot recall a worker already blocked inside a
multi-second call. Head-of-line blocking is defeated by **dedicated workers**, not by priority
numbers. The same applies here: a training job holding the CPU cannot be preempted by marking a
notification job "high priority".

## 2. Isolation of the CPU-bound lane

Training is the only lane that competes for CPU with everything else, so it is fenced at the
**container** level, not just by queue concurrency:

```yaml
trainer:
  cpus: 1.5              # hard ceiling — cannot starve notifier/api
  mem_limit: 2g
  environment:
    TRAIN_CONCURRENCY: 1
```

Queue concurrency alone is insufficient: one training job can saturate every core through BLAS
threading. Also pin `OMP_NUM_THREADS` / `OPENBLAS_NUM_THREADS` inside the trainer so numpy does not
quietly spawn a thread per core.

**With the shared embedding cache, training is far cheaper than it looks.** The expensive part of a
retrain is embedding the corpus, and those vectors are already computed and shared
(`HARVESTER_PLAN.md` §7). What remains is fitting a classifier on ~2,000 × 768 floats — seconds, not
minutes. The trainer's concurrency of 1 is therefore not a throughput problem at this scale; it is
cheap insurance.

## 3. Queue implementation — Postgres, `SKIP LOCKED`

```sql
CREATE TABLE jobs (
  id           BIGSERIAL PRIMARY KEY,
  lane         TEXT NOT NULL,                    -- notify | train | docgen | label
  user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  dedup_key    TEXT,                             -- collapses duplicate pending work
  payload      JSONB NOT NULL,
  state        TEXT NOT NULL DEFAULT 'ready',    -- ready | claimed | done | dead
  attempts     INT  NOT NULL DEFAULT 0,
  run_after    TIMESTAMPTZ NOT NULL DEFAULT now(),
  claimed_at   TIMESTAMPTZ,
  claimed_by   TEXT,
  last_error   TEXT
);
CREATE UNIQUE INDEX ON jobs (lane, user_id, dedup_key) WHERE state IN ('ready','claimed');
CREATE INDEX ON jobs (lane, state, run_after);
```

Claim:

```sql
UPDATE jobs SET state='claimed', claimed_at=now(), claimed_by=$worker, attempts=attempts+1
WHERE id = (
  SELECT id FROM jobs
  WHERE lane=$lane AND state='ready' AND run_after<=now()
  ORDER BY run_after
  FOR UPDATE SKIP LOCKED
  LIMIT 1
) RETURNING *;
```

Chosen over Redis because it is transactional with the data the job operates on — a training job and
the `model_version` bump it produces commit together, so "queue says done, DB says not" is
structurally impossible. Revisit above ~100 users.

### Duplicate collapse

The partial unique index on `(lane, user_id, dedup_key)` is what stops a user with 5 new labels
triggering 5 retrains. Enqueue is `INSERT ... ON CONFLICT DO NOTHING` with
`dedup_key = 'retrain'`. Five labels arrive → one pending retrain. This is the debounce, enforced by
the database rather than by application logic that can race.

## 4. Restart safety — the "doesn't get stuck" requirement

Three mechanisms, all required:

1. **Claim expiry.** A sweeper returns `claimed` rows whose `claimed_at` is older than the lane's
   visibility timeout to `ready`. A worker killed mid-job costs one retry, never a lost job. Timeout
   per lane: `notify` 2 min, `train` 30 min, `docgen` 10 min, `label` 30 min.
2. **Bounded attempts → dead letter.** After `MAX_ATTEMPTS` (5), state becomes `dead` and an admin
   alert fires. A permanently broken job must never retry forever.
3. **Unconditional re-arm for `notify`.** `next_run_at` for a user is advanced **whether the delivery
   succeeded or failed**. This is the specific fix for "a user's feed stops forever because one job
   errored" — the single most likely silent failure in the whole platform, and the one users would
   notice last.

Every worker is idempotent by design, because at-least-once delivery is what this protocol gives:

- `notify` — the per-user `seen` table has PK `(user_id, job_id)`; re-running a delivery re-sends
  nothing already recorded. Insert the seen row **in the same transaction** as marking the job done.
- `train` — writes to a temp artifact then atomically renames (existing behaviour, keep it). A retry
  overwrites cleanly.
- `docgen` — keyed by `(user_id, job_id)`; a rebuild replaces the previous artifact.
- `label` — labels are upserted on `(user_id, job_id)`.

## 5. Fairness

Within a lane, ordering is by `run_after`, which is FIFO in practice. That is fine for `notify` and
`docgen` (naturally spread). For `train` and `label`, where one user can enqueue a burst, add
round-robin selection:

```sql
-- pick the oldest ready job of the user who has waited longest since their last completion
ORDER BY (SELECT COALESCE(MAX(finished_at), 'epoch') FROM jobs j2
          WHERE j2.user_id = jobs.user_id AND j2.lane = jobs.lane AND j2.state='done'),
         run_after
```

Combined with duplicate collapse (§3), a single user cannot occupy the trainer. Tested explicitly —
`TEST_STRATEGY.md` T6.

## 6. Notification timing

```
users.notify_interval_min   -- bounded 15 .. 1440, validated (TENANT_SAFETY.md §4)
users.next_run_at
```

A dispatcher loop (in `api` or a small `ticker` service) runs every 60 s:

```sql
INSERT INTO jobs (lane, user_id, dedup_key, payload)
SELECT 'notify', id, 'due', '{}' FROM users
WHERE active AND next_run_at <= now()
ON CONFLICT DO NOTHING;

UPDATE users SET next_run_at = now() + (notify_interval_min || ' minutes')::interval
WHERE active AND next_run_at <= now();
```

`next_run_at` advances at **enqueue** time, not completion — so a slow or failed delivery cannot cause
drift or a stall. Precision is ±60 s, which is far inside what a job feed needs.

**Telegram rate limits are per-bot, and each user brings their own bot token** (the existing
`FRIENDS_ROLLOUT` pattern). So limits do not pool across users and there is no shared-bot throttling
wall. Retain a per-user send throttle (~20 messages/min) and a daily delivery cap
(`TENANT_SAFETY.md` §3) to keep a badly-configured account from spamming its owner.

`next_run_at` arithmetic uses the user's timezone (`users.timezone`), which matters for digest-style
intervals and for the per-day caps that reset at their local midnight.

### Delivery failure — the bot is gone (gap G6)

The likeliest real-world failure: the user blocks the bot, deletes the chat, or revokes the token.
Telegram answers 403 / chat-not-found. Retrying that is pointless — it will never succeed — and
dead-lettering it quietly means the user's feed simply stops with no signal to anyone.

```
on 403 / chat_not_found:
    users.telegram_status = 'broken'
    stop enqueuing notify jobs for this user
    email them reconnect instructions        (OPERATIONS_PLAN.md §6)
    surface the state on their dashboard     (OPERATIONS_PLAN.md §2)
```

Do **not** count this against `MAX_ATTEMPTS` — it is a terminal condition, not a transient error, and
treating it as a retry wastes five attempts before going quiet anyway. Reconnecting clears the flag
and resumes delivery.

## 7. Trigger policy for training

Event-driven with debounce, never cron:

```
on new label / feedback for user U:
    mark U dirty
    enqueue (train, U, dedup_key='retrain', run_after = now() + TRAIN_DEBOUNCE_MIN)
```

`TRAIN_DEBOUNCE_MIN` = 10. A user labelling 20 jobs in a burst produces exactly one retrain, ten
minutes after the last one. Add a floor of `TRAIN_MIN_NEW_LABELS` = 5 so trivial changes do not
retrain at all — a model refit on one extra label is not worth the CPU.

On completion the trainer bumps `users.model_version`; the scorer notices on next serve and reloads
(`PLATFORM_ARCHITECTURE.md` §7). No container restart, which is what today's flow requires and what
does not survive multi-tenancy.
