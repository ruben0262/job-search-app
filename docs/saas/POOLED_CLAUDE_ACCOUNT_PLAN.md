# Pooled Claude Account — Shared Broker + Prepaid Wallet Plan

**Status:** planning only, no code yet, and **deferred** — the platform is bring-your-own-key
(D2). Extended the Claude API backend plan (deleted 2026-07-31)
§8b (the "one shared org key" branch) and mirrors the ops pattern in
`services/embedder/docs/PLAN.md`.

**What this is:** one Anthropic org/API key funds all users. A shared Docker **broker service** (own
HTTP API) accepts scoring requests from every instance, batches them, calls Haiku once per batch,
routes results back, and **meters each user's spend against a prepaid wallet** they topped up. Users
see their own balance/usage through the broker (not through Anthropic — they have no Anthropic account
of their own in this model).

**Why this branch instead of BYOK:** with one key, the batch discount, prompt cache, and rate-limit
pool are finally shared across users (BYOK cannot — see §8b). The trade is that **you become a
custodian of other people's money and a reseller of Anthropic access** — read §9 (Risks/Legal) before
building; that is the real gate, not the code.

---

## 1. Architecture

```
 instance A ─┐                          ┌─ Anthropic API (ONE org key)
 instance B ─┼─HTTP(+token)─▶ BROKER ───┤   • Batch API (-50%)
 instance C ─┘   (Docker)     │         └─ Admin/Usage API (reconciliation)
                              │
                    ┌─────────┴──────────┐
                    │ SQLite (WAL)       │  single-writer, append-only ledger
                    │  accounts, ledger, │
                    │  requests, prices, │
                    │  reconciliation    │
                    └────────────────────┘
```

- **Broker = single process** (like the embedder). It is the **only** holder of the Anthropic key —
  instances never see it. Instances authenticate with a per-user broker token.
- **Single writer** to SQLite (WAL mode) → no lost-update races on the money ledger. All balance
  mutations go through one serialized code path.
- Endpoints (sketch): `POST /score` (submit jobs), `GET /result/{batch_id}`, `GET /balance`,
  `GET /usage?period=month`, `POST /deposit` (admin-gated), `GET /health`.
- Result delivery: broker submits **one Anthropic Batch** spanning many users' jobs, tags each request
  with `custom_id = "{user_id}:{job_id}"`, polls to `ended`, then fans results back per user.

---

## 2. The money model — prepaid wallet, event-sourced, integer-only

The user's hard requirement: **no bugs in tracking money deposited or tokens spent per account.** The
design choices below exist specifically to make money bugs structurally hard.

### 2.1 Non-negotiable rules

1. **Money is never a float.** Store every amount as an **integer in nanoUSD** (1 USD = 1e9 nano). All
   arithmetic is integer. Floats drift; integers do not. Display converts to dollars only at the edge.
2. **The ledger is append-only.** Balances are **never** mutated in place. A balance is *derived* by
   folding immutable ledger entries. You can replay the whole ledger and get the same numbers — that is
   the audit guarantee.
3. **Every debit is idempotent.** Keyed by the Anthropic `request_id` (or batch `custom_id`) +
   entry-type. A retried settle is a no-op, not a double charge.
4. **Pre-authorize before spend.** A request cannot run unless the user's *available* balance covers a
   conservative cost estimate, which is **held** up front. This makes overspend impossible even under
   concurrency — you can't hold what isn't there.
5. **Every debit records the price-table version it used.** Prices change; a debit must be reproducible.

### 2.2 Ledger entry types (each row immutable)

| Type | Effect on `available` | Effect on `held` | When |
|---|---|---|---|
| `deposit` | `+amount` | — | user tops up the pool |
| `hold` | `−estimate` | `+estimate` | request pre-authorized, before the call |
| `settle` | `+(estimate − actual)` | `−estimate` | usage known: charge `actual`, refund the over-estimate |
| `release` | `+estimate` | `−estimate` | request failed/cancelled: full refund of the hold |
| `refund` | `+amount` | — | manual/goodwill credit |
| `adjust` | `±amount` | — | reconciliation correction (audited) |

**Derived quantities (fold the ledger, never store):**
- `available = Σ deposit + Σ refund + Σ adjust + Σ(settle.Δavailable) + Σ(release.Δavailable) − Σ hold`
- `held = Σ hold − Σ settle.est − Σ release.est`  (sum of *open* holds)
- `spent = Σ settle.actual`
- **Invariants checked on every write:** `available ≥ 0`, `held ≥ 0`, and
  `available + held + spent == Σ deposits + Σ refunds + Σ adjust`. If that identity ever breaks, the
  broker refuses further debits and alerts — a tripped invariant means a bug, and it halts before
  corrupting more state.

### 2.3 Schema (SQLite, WAL)

```sql
accounts(user_id TEXT PK, created_at, status,
         monthly_cap_nano INTEGER,            -- per-user spend cap (0 = pool share only)
         broker_token_hash TEXT)

ledger(id INTEGER PK, user_id, ts, type,      -- deposit|hold|settle|release|refund|adjust
       available_delta_nano INTEGER,          -- signed
       held_delta_nano INTEGER,               -- signed
       actual_nano INTEGER,                   -- spend recorded on settle (else 0)
       ref_id TEXT,                           -- request_id / custom_id / deposit_id
       rate_version INTEGER,
       meta_json TEXT,
       UNIQUE(ref_id, type))                  -- idempotency

requests(request_id TEXT PK, user_id, batch_id, job_id,
         est_nano INTEGER, actual_nano INTEGER,
         status,                              -- held|settled|released|errored
         usage_json TEXT, created_at, settled_at)

price_table(rate_version INTEGER, klass TEXT, -- input|output|cache_read|cache_write|batch_input|batch_output
            nano_per_token INTEGER, effective_from, model TEXT)

reconciliation(period, attributed_nano INTEGER, invoice_nano INTEGER,
               drift_nano INTEGER, ts, note)
```

### 2.4 Cost computation (from Anthropic `usage`)

Anthropic returns per-request usage: `input_tokens`, `output_tokens`, `cache_read_input_tokens`,
`cache_creation_input_tokens`. Cost is the **sum over token classes × the class's rate**:

```
actual_nano = input_tokens        × rate[batch_input  if batch else input]
            + output_tokens       × rate[batch_output if batch else output]
            + cache_read_tokens   × rate[cache_read]
            + cache_create_tokens × rate[cache_write]
```

- Batch requests use the `batch_*` rates (Haiku −50% on in/out). Cache reads bill at ~0.1×; cache
  writes at ~1.25×. **Attribute each token to the exact class** — mixing them is the classic accounting
  bug.
- Rates come from `price_table` at the debit's `rate_version`. Never hardcode a rate at the call site.

### 2.5 Pre-auth estimate (the hold)

Before a batch, estimate each request conservatively so the hold can never *under*-cover:

```
est_nano = count_tokens(prompt) × rate[input] + max_tokens × rate[output]
```

`count_tokens` is exact for input; `max_tokens` is the worst-case output ceiling. Real output is almost
always far less, so `settle` refunds most of the hold. The hold is intentionally pessimistic — it
guarantees a user can never drive the pool negative, at the cost of briefly over-reserving.

---

## 3. Request lifecycle (money-safe path)

1. **Submit** — instance `POST /score` with jobs + broker token → resolve `user_id`.
2. **Estimate & pre-auth** — `count_tokens` each job; compute `est_nano`. Check
   `available ≥ Σ est` **and** `month_spent + Σ est ≤ monthly_cap`. If not → reject with a clear
   "insufficient balance / cap reached" (do not partially run silently). Write one `hold` per request
   inside a single DB transaction.
3. **Enqueue** into the fair scheduler (§4); jobs join the next Anthropic batch.
4. **Batch call** — broker submits one `messages.batches.create` spanning users; each request
   `custom_id = user_id:job_id`.
5. **Poll → results** — on `ended`, for each result:
   - success → read `usage`, compute `actual_nano`, write `settle` (idempotent on `custom_id`), mark
     `requests.status=settled`.
   - error/expired → write `release` (full refund), `status=errored`. **Never charge for a failed
     request.**
6. **Fan out** — deliver each user's results to their instance (`GET /result` or push).
7. **Reconcile** (nightly, §5).

Idempotency everywhere: replaying step 5 for an already-settled `custom_id` hits the `UNIQUE(ref_id,
type)` constraint and is skipped — safe against poll retries, restarts, duplicate webhooks.

---

## 4. Rate-limit safety (one account, many users)

One org = **one** ITPM/OTPM/RPM pool + batch concurrency limits. Everyone shares it, so the broker must
govern it or one user's dump stalls everyone.

- **Global token-bucket governor** sized to the org's ITPM/OTPM. The broker never dispatches faster
  than the bucket refills; overflow queues.
- **Weighted-fair scheduling across users** — round-robin / WFQ so a 5000-job submission from user A
  interleaves with user B's 50 jobs instead of blocking them.
- **Per-user in-flight cap** and **per-user monthly spend cap** (`accounts.monthly_cap_nano`) — a user
  cannot monopolize rate *or* drain the pool.
- **Bounded in-flight batches** — respect the org's batch concurrency limit; queue beyond it.
- **Backpressure** — on 429, exponential backoff at the governor (not per-request), and surface queue
  depth via `/usage` so users see "you're queued", not a silent stall.

---

## 5. Reconciliation (attributed spend vs the real bill)

Per-request attribution will **drift** from the Anthropic invoice (rounding, price-table lag,
un-attributable overhead). Reconcile, don't assume:

1. Nightly, pull the org's real usage/cost from the **Anthropic Admin/Usage API** for the period.
2. Compute `attributed_nano = Σ requests.actual_nano` for the same window.
3. Store `drift = invoice − attributed`. If `|drift|` exceeds a threshold (e.g. > 1% or > $0.50),
   **alert** (Telegram) — drift beyond noise means a bug in the price table or attribution.
4. Small expected drift can be absorbed into a pool "house" account via an audited `adjust`; large
   drift is investigated before any deposit/withdrawal is trusted.

This closes the loop: the sum of what you charged users must track what Anthropic charged you.

---

## 6. User visibility

Pooled means **no per-user Anthropic dashboard** — the broker is their dashboard. Expose per token:

- `GET /balance` → `{deposited, spent, available, held, monthly_cap, month_spent, month_remaining}`
  (all rendered in dollars from nanoUSD at the edge).
- `GET /usage?period=month` → tokens + cost per day, per model, this month; cap headroom.
- Surface both through each instance's existing Telegram bot (a `/balance` command reusing
  `pipeline/notifier.py`), so a user checks their pool wallet without a web UI.
- Every deposit and settle is itemized (the ledger is the statement) — a user can be shown their own
  immutable line items on request.

---

## 7. Token / cost optimization on the one account

Now that it is **one org key**, the pooling optimizations BYOK couldn't reach become real:

1. **Shared prompt cache (the pooling win).** The scoring **rubric + system prefix is byte-identical
   across users**, so cache it once; every user's batch reads the same warm cache entry (~0.1× on the
   cached prefix). With steady traffic the 5-min TTL never expires → near-continuous cache hits for the
   whole pool. **Structure the prompt so the shared part is the cacheable prefix**:
   `[generic scoring instructions — CACHED, shared] → [per-user profile block] → [jobs JSON]`. Only the
   leading shared block caches across users; a per-user block placed earlier would break the shared
   prefix. Verify with `usage.cache_read_input_tokens > 0`. (Haiku's 4096-token min still applies — if
   the shared block is under it, no cache; measure first.)
2. **Batch −50%.** One batch spanning all users' offline scoring. Same discount, fewer polls, shared
   rate budget. Sync paths (watcher/verify) stay per-request, full price.
3. **Cross-user *result* dedup — only if rubrics match.** If two users share the **same** scoring
   rubric/profile and the **same** job posting (EU-remote searches overlap heavily), the score is
   identical → cache it keyed by `(job_content_hash, rubric_version)` and **skip the LLM call entirely**
   for duplicates. This is the biggest possible saving *but only applies to users on an identical
   rubric*; different profiles → different scores → no output dedup (fall back to #1's prefix cache).
   Do not dedup across differing rubrics — it would return a wrong score.
4. **Slimming (already in place):** 2500-char description cap, minified JSON, only rubric-scored fields,
   `max_thinking_tokens=0` for scoring.
5. **Structured output / JSON mode** — removes malformed-output retries (wasted tokens).
6. **`count_tokens` pre-sizing** — right-size batches and holds; avoid oversized single calls that
   truncate and retry.
7. **No re-scoring** — a shared score store (keyed by content-hash + rubric-version) also stops
   *re-scoring the same posting across runs/instances*, not just within one.

**Realistic stack for pooled offline scoring:** `batch (0.5×) × shared-prefix-cache (partial) −
duplicate-job skips`. The duplicate-skip term is the one only pooling unlocks; its size depends entirely
on how much users' searches and rubrics overlap.

---

## 8. Correctness test plan (before real money touches it)

1. **Ledger property tests** — random sequences of deposit/hold/settle/release/refund; assert the §2.2
   invariant (`available + held + spent == deposits + refunds + adjust`) holds after every op, and
   `available ≥ 0` always.
2. **Idempotency tests** — replay every settle/release twice; balances unchanged.
3. **Concurrency test** — hammer the single-writer path with parallel submits; assert no overspend and
   no lost deposit.
4. **Attribution test** — feed known `usage` payloads (incl. cache + batch classes) and assert
   `actual_nano` matches a hand-computed figure at a fixed `rate_version`.
5. **Reconciliation dry-run** — synthetic invoice vs attributed sum; assert drift computed and alerted
   correctly at the threshold.
6. **Failure injection** — batch with some errored `custom_id`s; assert only successes are charged and
   the rest fully released.
7. Only after all green: a **small real pilot** (your own money, one or two friendly users) with a low
   monthly cap, watched for a full billing cycle against the real invoice.

---

## 9. Risks, legal, non-goals

- **⚠ Anthropic ToS / reselling.** Pooling other people's money to buy API access under one org key is
  **reselling / sublicensing Claude access**. This may require Anthropic's explicit permission and can
  breach the usage policy if done without it. **This is the real gate — confirm the ToS position with
  Anthropic before collecting any user money.** Do not build the deposit flow until this is cleared.
- **⚠ Custodian of funds.** Holding user deposits makes you a money custodian — refunds, disputes, a
  user leaving with a positive balance, and possibly local financial-conduct rules all apply. Keep the
  pilot to friends and your own funds until the model is proven and the legal position is understood.
- **Single point of failure / key blast radius.** The one org key funds everyone; a leak or a runaway
  loop drains the shared pool. Mitigations: key lives only in the broker, per-user + global caps, the
  Console workspace spend limit as the ultimate backstop, and the pre-auth hold that blocks overspend.
- **Rate contention.** One user's bulk run can queue others (§4 fair scheduling mitigates; surface
  queue depth so it is visible, not silent).
- **Drift / money bugs.** The whole of §2 exists to prevent these; §5 catches what slips through; §8
  proves it before real money.
- **Non-goal:** replacing BYOK for people who prefer their own key/account — the pooled broker is an
  *option*, not a forced migration. A per-instance API backend still
  stands on its own.
- **Non-goal:** touching any scoring rubric or writing prompt (SACRED; transport/metering only).

---

## 10. Summary

One Anthropic org key + a single-writer Docker **broker** that batches Haiku across users, meters each
user against a **prepaid wallet** kept as an **append-only, integer-nanoUSD, pre-authorized,
idempotent** ledger, governs the **one shared rate pool** with fair scheduling, and **reconciles**
attributed spend against the real Anthropic invoice nightly. Users see their balance/usage through the
broker (their dashboard), not through Anthropic. Pooling unlocks the optimizations BYOK could not —
**one shared warm prompt cache** and **duplicate-job skips when rubrics match** — on top of batch −50%.
The build is realistic and the ledger design is buildable bug-resistantly; the **actual gate is the
Anthropic ToS reselling question and the custodian-of-funds reality (§9)** — clear those before any
deposit code is written.
