# Harvester — one shared scrape serving every user

**Status:** design, 2026-07-26. Depends on `PLATFORM_ARCHITECTURE.md` §1 (shared/per-user split).
**Reference:** `job-search/pipeline/` — the working single-tenant harvester. This plan de-personalises
it; the source adapters, filters, and dedup logic are kept as-is.

---

## 1. The insight that shrinks this problem

The brief describes accumulating every user's search terms into a set and scraping the union. That is
right for one tier of sources and unnecessary for the other:

| Tier | Today | User-dependent? |
|---|---|---|
| **Direct ATS** (Greenhouse, Ashby, Lever, SmartRecruiters, Recruitee, Breezy, Workday, Personio, Workable) | Polls ~900 harvested company tokens, ETag/304 conditional | **No.** It already harvests *everything* those companies post, for nobody in particular |
| **Board APIs** (Remotive, Himalayas, RemoteOK, WWR, Arbeitnow, NoFluffJobs, JustJoin, hiring.cafe, profession.hu, ...) | Pulls recent feeds | **No.** Feed-shaped, not query-shaped |
| **JobSpy tier** (LinkedIn, Indeed, Google) | keyword × location × remote-flag queries | **Yes.** This is the only query-shaped tier |

So roughly **two thirds of the sources are already shared-shaped and need no term merging at all** —
they are broad harvests that get filtered per user downstream. Only the JobSpy tier consumes a query
set, and it is also the tier that costs proxy bandwidth and carries ban risk.

**Note (D3, 2026-07-26):** LinkedIn within the JobSpy tier is executed **per user on their own proxy
credential** and is therefore outside the shared query-set merge below (§5.1). Indeed and Google need
no proxy and stay shared. Sections 2–3 apply to the shared query-shaped sources; a user's LinkedIn
query set is simply their own five terms, unmerged.

**Design principle: harvest broadly, match per user.** Do not attempt to construct per-user queries.
One broad corpus, N cheap filter+score passes over it. This is what makes per-user notification
intervals free (§6) and cold start instant (`COLD_START_PLAN.md`).

## 2. Query-set construction (JobSpy tier only)

A query is a normalised tuple, not a string:

```
QueryKey = (source, keyword_norm, location_norm, is_remote, max_age_hours_bucket)
```

- `keyword_norm`: lowercased, whitespace-collapsed, stop-words stripped, tokens sorted.
  `"Python Developer"`, `"python  developer"`, `"developer python"` → one key.
- `location_norm`: resolved against a controlled location vocabulary (the config form offers a
  picklist, not free text — `TENANT_SAFETY.md` §4), so `"London"` / `"London, UK"` / `"Greater London"`
  collapse.
- `max_age_hours_bucket`: rounded up to the coarsest bucket any requesting user asked for, so a user
  wanting 24 h and one wanting 72 h share the 72 h fetch. The 24 h user filters down locally.

Each cycle: fold all active users' configs into a `dict[QueryKey, set[user_id]]`, then execute each
distinct key **once**. The user set is retained only for attribution and quota accounting — results go
to the shared corpus regardless of who asked.

### Subsumption — deliberately deferred

`"python"` strictly contains `"python developer"`, so in principle the broader query could replace the
narrower and cut the set further. **Do not do this in v1.** Sources cap result counts, so a broader
query does not reliably return a superset — it returns a *truncated* superset, silently dropping the
narrow query's tail. Exact-tuple dedup is safe and captures most of the win (users overlap heavily on
common terms). Revisit only for sources with no result cap, and prove it with a diff against the
un-subsumed run.

## 3. Fan-out budget — the thing that actually needs limiting

Unbounded, 50 users × 5 unique terms × 3 JobSpy sources = 750 queries/cycle. That is a ban, a
bandwidth bill, and a 15-minute cycle that takes an hour.

**Global harvest budget, enforced before execution:**

| Knob | v1 value | Rationale |
|---|---|---|
| `HARVEST_MAX_QUERIES_PER_CYCLE` | 60 | Fits comfortably in a 15-min cycle at current per-query latency |
| `HARVEST_MAX_QUERIES_PER_USER` | 5 | Hard cap, same for everyone (`TENANT_SAFETY.md` §3) |
| `HARVEST_MAX_PROXY_MB_PER_DAY` | 500 | Circuit breaker; harvest degrades to non-proxied sources when hit |
| Outer source concurrency | 8 | Already the fix from CHANGELOG 2026-07-24 — do not raise it |

When the deduped query set exceeds the budget, queries are selected by **round-robin over users, not
by list order** — so every user gets their first term served before any user gets their second. A user
with 5 niche terms cannot crowd out four other users' primary term. Unserved queries carry priority
into the next cycle.

## 4. Cycle shape

This is the **shared** loop. The per-user LinkedIn loop (§5.1) runs the same steps 4–8 on its own
schedule with one user's credential and query set.

```
every HARVEST_INTERVAL_MIN (15, platform constant):
  1. build QueryKey set from active user configs        (cheap, in-memory)
  2. apply fan-out budget + round-robin selection
  3. execute: ATS tier (all) ∥ board tier (all) ∥ JobSpy tier (selected queries)
  4. dedup against corpus  — url → canonical → content_key   (as today)
  5. phase-1 filters (seniority, known-tech gate, blocklist) — as today, user-independent
  6. fetch descriptions for survivors, proxy only linkedin.com  (2026-07-26 routing)
  7. upsert into corpus; enqueue new job_ids for embedding
  8. embed via /embed/bulk, cache vector on the corpus row     (once, for everyone)
```

Steps 4–6 are lifted unchanged from `pipeline/scraper.py` + `pipeline/processor.py`. Step 8 is new and
is what makes the embedder scale (`§7`).

**Per-user scoring is NOT in this loop.** It is a separate, cheap pass (§6).

## 5. Proxy: strictly per-user (decision D3)

**A user's proxy credential is used only for that user's own fetches. Never pooled, never rotated
across users, never used on anyone else's behalf.**

Scope bounds the problem: after the 2026-07-26 routing change, **only `linkedin.com` is proxied**. The
ATS and board tiers — roughly two thirds of all sources (§1) — fetch direct. So this section governs
one tier, not the whole harvester. Keep `make_client()` as the only way harvester code builds an HTTP
client, so that stays true.

### 5.1 Two harvest loops, not one

```
SHARED LOOP     every 15 min, platform-wide, no proxy
                ATS tier + board tier + JobSpy Indeed/Google
                → shared corpus, deduped, embedded once

PER-USER LOOP   every user.linkedin_interval_min, that user's credential
                JobSpy LinkedIn, only that user's query set
                → same shared corpus (dedup + embed-once still apply)
                → rows tagged source='linkedin'
```

Both write to the same corpus, so storage, dedup, and embedding stay shared and the §4 sizing holds.
Only *who executes the fetch and whose credential pays* differs.

### 5.2 Visibility gate — no free-riding, by construction

LinkedIn rows are in the shared corpus, so without a gate a user with no credential would read results
others paid for. The gate:

> **A user's feed includes `source='linkedin'` rows only while `proxy_credentials.status='healthy'`
> for that user.**

One predicate on a column the corpus already records. No per-row provenance, no partitioning, no
duplicated storage. A user without a credential still gets the ~2/3 of sources that cost nothing —
a useful product on its own, and a clear reason to add a credential.

### 5.2b The per-user tier still needs a global governor

The fan-out budget in §3 governs the **shared** query set. When LinkedIn moved to per-user loops it
left that governor behind — so without this section, 50 users on independent schedules issue
uncoordinated LinkedIn volume with nothing able to throttle or stagger it.

Per-user proxies give IP diversity, which handles the ban risk. They do **not** handle the platform
issuing hundreds of concurrent LinkedIn requests, nor a thundering herd when many users share a
notification interval.

Three controls, all cheap:

| Control | v1 value | Purpose |
|---|---|---|
| `LINKEDIN_MAX_CONCURRENT_USERS` | 4 | Global ceiling on simultaneous per-user LinkedIn loops |
| Per-user jitter on `next_linkedin_at` | ±10% of interval | Stops users on the same interval firing together |
| Global kill switch | env flag | Skip the whole tier in one action if LinkedIn starts blocking broadly |

Per-user LinkedIn runs are enqueued as ordinary queue jobs (`SCHEDULER_PLAN.md`), so the concurrency
cap is just that lane's worker-pool size — no new mechanism. The kill switch matters because the
alternative during an incident is editing 50 users' configs.

### 5.3 Frequency is the cost lever

Because the LinkedIn tier is now per-user, its frequency can be per-user too:

```
users.linkedin_interval_min   -- default = notify_interval_min, floored at 60
```

A user on a 4-hour digest does not need LinkedIn scraped 96 times a day. **This saving was not
available under any shared design** and materially reduces per-user GB. The shared loop stays at 15
minutes because it costs nothing.

### 5.4 Schema

```sql
CREATE TABLE proxy_credentials (
  user_id       UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  secret_ref    TEXT NOT NULL,        -- encrypted, TENANT_SAFETY.md §2
  daily_cap_mb  INT  NOT NULL DEFAULT 200,
  mb_used_today NUMERIC NOT NULL DEFAULT 0,
  status        TEXT NOT NULL DEFAULT 'healthy',  -- healthy | quarantined | disabled
  fail_streak   INT  NOT NULL DEFAULT 0,
  last_error    TEXT,
  last_used_at  TIMESTAMPTZ
);
```

Attribution is now trivially correct — there is only ever one candidate credential. Measure bytes per
response (`response.num_bytes_downloaded` plus a request-size estimate), add to the counter in the
same transaction that records the fetch, reset at the user's local midnight (§ timezone, `GAPS_REVIEW.md`
G7).

### 5.5 Health and degradation

- N consecutive failures (auth error, connect error, 407) → `quarantined`; notify the owning user that
  their proxy credential is failing, and **tell them their LinkedIn results have paused** — silence
  here would look like the product breaking. Retry on backoff, not permanently disabled.
- At `daily_cap_mb` → skip until reset, surfaced in the dashboard as a quota state, not an error.
- **No credential is not an outage.** That user's shared-tier feed continues untouched.

### 5.6 What this costs

Two users searching the same terms both fetch — there is no cross-user dedup on this tier any more.
That is the accepted price of no pooling, and it buys the elimination of both the free-rider problem
and the proxy-ToS exposure.

It stays affordable because the tier is small (LinkedIn only) and §5.3 cuts frequency. **Size
`daily_cap_mb` from a real measurement before onboarding anyone** — instrument the reference instance
for a week against the DataImpulse dashboard rather than trusting a planning estimate.

## 6. Decoupling harvest frequency from notification frequency

**Harvest interval is a platform constant (15 min). Notification interval is a user setting
(bounded 15 min – 24 h).** They are unrelated:

- The harvester writes new jobs into the corpus continuously.
- A user's notification run reads *corpus jobs newer than their last delivery*, filters by their
  config, scores with their model, and delivers.

So a user asking for hourly digests costs nothing extra to scrape — they just read a wider slice. A
user asking for 15-minute alerts costs one extra cheap scoring pass. **Per-user intervals are almost
free**, which is only true because harvest is shared.

Per-user scoring pass cost: filter is an indexed query; scoring is a matrix multiply against cached
vectors. 10 users × 1,500 new jobs/day = 15,000 classifier inferences/day. That is milliseconds of
actual compute.

## 7. Embedding, once

The corpus row holds the vector. The flow is:

```
new job → text hash → is the vector already on the row?
                        yes → done, zero embedder load
                        no  → /embed/bulk → store on row
```

**Each job is embedded exactly once, ever, no matter how many users score it.** This is the direct
answer to "will the embedder keep up with 5 users" — it never sees 5× load, because users share the
cache. Embedder load scales with *new jobs per day*, which is a platform constant, not with user
count.

The existing two-lane dispatcher then does its job: harvest embedding goes to `/embed/bulk` (few
workers, small chunks), and anything a waiting user needs goes to `/embed` (own worker pool, never
blocked behind a bulk flood). That design was validated on the real VPS after a single-priority-queue
version regressed score latency from 0.08 s to 8–15 s under load — do not revisit it.

## 8. What must change in the existing code

| Change | Where | Size |
|---|---|---|
| Config source: single YAML → union over user configs | `pipeline/lib/config.py` | Small |
| Store: SQLite `.connect()/.load()` → Postgres corpus repo | `pipeline/lib/store.py` | **Large** — the real work |
| Persist embedding vector on the corpus row | new | Small |
| Remove per-user assumptions from scoring path | `pipeline/processor.py` | Medium |
| Query-set builder + fan-out budget | new module | Medium |
| Split scoring out of the harvest loop | `pipeline/processor.py` | Medium |

`store.py` is 578 lines and is the load-bearing piece. Its two access modes (`.connect()` hot loop vs
`.load()` materialised) map onto a repository interface cleanly, and the dedup semantics
(canonical URL folding, location-aware content keys) must be preserved **exactly** — they are the
"never see the same job twice" guarantee and they are already tuned. Port them behaviourally, with the
dedup test suite (`TEST_STRATEGY.md` T2) written *before* the port.
