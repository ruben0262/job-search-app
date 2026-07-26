# Platform Architecture — shared harvest core, per-user tail

**Status:** design, agreed 2026-07-26. This is the spine document; the subsystem plans
(`HARVESTER_PLAN.md`, `SCHEDULER_PLAN.md`, `COLD_START_PLAN.md`, `TENANT_SAFETY.md`,
`TEST_STRATEGY.md`) hang off it. Read this one first.

**Supersedes:** `SAAS_PLATFORM_PLAN.md` §4 (which recommended Model 1, one Docker Compose project
per user) and §7's open decisions. That recommendation is now **rejected** — see §2. The rest of
`SAAS_PLATFORM_PLAN.md` (legal risk §5a, operational burden §5f, framework constraints §5g) still
stands and is not restated here.

**Reference implementation:** `d:\Projects\job-search` is a working single-tenant instance of this
exact pipeline. Every design below is a de-personalisation of something already proven there, not a
green-field invention. Where this document says "as today", it means that repo's behaviour is the
spec.

---

## 1. The one decision everything else follows from

**Job postings are public, identical for every user, and expensive to acquire. Fit is private,
personal, and cheap to compute.**

Split the system on that line:

| Layer | Shared | Per-user | Why |
|---|---|---|---|
| Scraping / fetching job pages | ✅ | | Same bytes for everyone; N× duplicate fetch is pure waste and N× ban risk |
| Job corpus (text, metadata, dedup keys) | ✅ | | 50× storage saving, measured in §4 |
| Embedding vectors | ✅ | | Deterministic function of the text. Same input → same vector, forever |
| Fit score | | ✅ | This IS the product |
| Classifier weights | | ✅ | Trained on their labels |
| Seen / notified state | | ✅ | |
| Labels & feedback | | ✅ | |
| Config, credentials, Telegram bot | | ✅ | |
| Generated CVs / covers | | ✅ | |
| Claude credential + spend | | ✅ | Metered, personal |
| Proxy bandwidth | ⚠️ **split** | ⚠️ | See §6 — this is the one place the plan was self-inconsistent |

Everything downstream — scaling, cost, storage, the cold-start advantage, per-user notification
intervals — is a consequence of this table.

## 2. Why not one container stack per user

`SAAS_PLATFORM_PLAN.md` §4 recommended cloning the whole Compose project per tenant (Model 1),
because isolation comes free. Rejecting it, for reasons that are now measured rather than assumed:

- **RAM.** Each stack runs its own `scorer-service` (steady ~1.9 GB, `mem_limit: 3g`). Five users =
  ~10 GB before any other service. The 2026-07-24 VPS incident was this exact problem at N=2.
- **Storage.** 44 GB at 50 users vs 0.87 GB shared (§4).
- **Bandwidth + ban risk.** N stacks each scrape LinkedIn independently. N× proxy GB for byte-identical
  pages, and N× the scraping footprint against sources that are already grey-area (`SAAS_PLATFORM_PLAN.md`
  §5a).
- **No cold-start advantage.** A per-user stack starts with an empty database. A shared corpus gives a
  brand-new user instant access to the entire history — this is the difference between a product that
  works on day one and one that works in week three (`COLD_START_PLAN.md`).

Isolation is instead enforced at the **data layer** (`user_id` on every per-user row, FK-constrained,
tested — `TEST_STRATEGY.md` T1) rather than the container layer. That is a higher correctness bar and
it is accepted deliberately, with the isolation test treated as a release blocker.

## 3. Container topology

Services are split so any one can be rebuilt and restarted without taking the platform down. That is
a hard requirement (regular updates), and it constrains the contracts between them — see §5.

```
                                  ┌──────────┐
   browser ──────────────────────▶│   web    │  React SPA (static, CDN-able)
                                  └────┬─────┘
                                       │ REST
                                  ┌────▼─────┐
                                  │   api    │  FastAPI: auth, config, dashboard, admin
                                  └────┬─────┘
                                       │
        ┌──────────────┬───────────────┼───────────────┬──────────────┐
        │              │               │               │              │
  ┌─────▼─────┐  ┌─────▼─────┐   ┌─────▼─────┐   ┌─────▼─────┐  ┌─────▼─────┐
  │ harvester │  │  trainer  │   │  scorer   │   │ notifier  │  │  docgen   │
  │ (shared)  │  │ (queue,   │   │ (N models │   │ (per-user │  │ (Chrome,  │
  │  scrape   │  │  conc. 1) │   │  in LRU)  │   │  bots)    │  │  queue)   │
  └─────┬─────┘  └─────┬─────┘   └─────┬─────┘   └─────┬─────┘  └─────┬─────┘
        │              │               │               │              │
        └──────────────┴───────┬───────┴───────────────┴──────────────┘
                               │
                    ┌──────────▼──────────┐        ┌──────────────┐
                    │     postgres        │        │   embedder   │
                    │ corpus + tenant data│        │ TEI + gateway│  ALREADY BUILT
                    │ + job queues        │        │ (two-lane)   │
                    └─────────────────────┘        └──────────────┘
```

| Service | Restart blast radius | Notes |
|---|---|---|
| `web` | none (static) | |
| `api` | users cannot log in / edit config; pipeline keeps running | |
| `harvester` | new jobs stop arriving; existing feeds unaffected | de-personalised `pipeline/` |
| `embedder` | scoring + training pause, fail **closed** (never scores against a wrong vector space) | built; `services/embedder/` |
| `scorer` | new jobs queue unscored, drain on restart | must hold N user models (§7) |
| `trainer` | retrains queue up | concurrency 1 |
| `notifier` | deliveries queue up, `next_run_at` re-armed | one bot token per user |
| `docgen` | CV builds queue up | slow (~90-130 s/CV), isolated for that reason |
| `postgres` | everything stops | the one true single point of failure — back it up |

**Decision: no Redis.** Job queues live in Postgres using `SELECT ... FOR UPDATE SKIP LOCKED`. At
<50 users that is comfortably fast, it is transactional with the data the jobs operate on (no
"queue says done, DB says not"), and it removes a service with its own persistence story. Revisit
above ~100 users or when queue throughput exceeds ~100 jobs/s. See `SCHEDULER_PLAN.md` §3.

## 4. Storage model and sizing

### Measured per-job footprint (from the live single-tenant repo, 2026-07-26)

| Item | Bytes | Note |
|---|---|---|
| Dedup index entry | 258 | url + canonical + content_key |
| Scored job + description | 3,381 | the JD text dominates everything else |
| Label row (today) | 2,937 | **duplicates the description** — fixed below |
| Embedding vector (768 × f32) | 3,072 | shared; computed once, ever |

### Projection at 1,500 new unique jobs/day

| | Shared design | Per-user copies |
|---|---|---|
| Corpus, 90-day description retention | **0.87 GB steady state** | 44 GB at 50 users |
| Cold dedup index (forever) | 0.14 GB/year | ×N |
| Per-user tail @150 scored/day | 5.5 MB/user/year | — |
| 50 users' tails | 0.27 GB/year | — |

**Conclusion: storage is not a problem, provided nothing per-user stores job text.** That is the whole
ballgame. Two rules enforce it:

1. **A per-user row never contains job text.** Labels, scores, feedback, and applied-records reference
   `job_id`. This alone takes a label row from 2,937 B to ~200 B.
2. **Tiered retention.** A job keeps its description + vector for 90 days (`CORPUS_HOT_DAYS`). After
   that, if no user has acted on it, the description and vector are dropped and the 258-byte dedup key
   is kept forever. Jobs any user has applied to, built a CV for, or labelled are pinned and never
   trimmed.

### Database choice — Postgres, not MongoDB

**Recommendation: Postgres for everything, including user accounts and config.** Reasoning:

- The corpus is relational-shaped and the hot path is *indexed dedup lookup* + *per-user join*. That is
  precisely why this repo already migrated its dedup store JSON → SQLite (CHANGELOG 2026-07-24). The
  multi-tenant version of that migration is Postgres, not Mongo — the access pattern did not change,
  only the number of concurrent writers (which is what SQLite's single writer cannot take).
- Tenant isolation wants FK constraints and, later, row-level security. `user_id UUID REFERENCES
  users(id) ON DELETE CASCADE` makes account deletion correct by construction (§8) instead of by
  remembering to write cleanup code.
- JSONB covers every "flexible document" need the config forms have. There is no schema-flexibility
  argument left that Mongo wins.
- Running one datastore instead of two is a real operational saving at this size.

**If you prefer Mongo for user data anyway**, it works — the cost is that per-user score joins against
the corpus move into application code, and cascade-delete becomes manual. Flag it as a decision, do not
let it happen by default. This is open decision **D1** (§9).

### Every store, not just the corpus

Earlier drafts designed the corpus port and left the rest implicit. The reference instance has ~10
persistent stores and each needs a decision:

| Store | Today | Multi-tenant home | Notes |
|---|---|---|---|
| `jobs.db` (`all_links`, `processed`) | SQLite | **Shared** `corpus` table | The main port; `HARVESTER_PLAN.md` §8 |
| `ats_tokens.json` (~900 tokens) | JSON | **Shared** table | Harvester state, benefits everyone |
| `ats_etags.json` + `.lock` | JSON + **file lock** | **Shared** table | ⚠️ The file lock does not survive multiple writers — replace with a row-level upsert. This store is what makes ATS polling near-free (~40s cold, near-free warm); breaking it means re-downloading every board every cycle |
| `new_links.json` | JSON queue | **Shared**, or fold into `corpus.state` | Transient |
| `labels.jsonl` | JSONL | **Per-user** table, referencing `job_id` | Drops ~2,937 B → ~200 B per row (§4) |
| `pending_labels.jsonl` | JSONL | Covered by the `label` queue lane | `SCHEDULER_PLAN.md` |
| `feedback` (CV-built, `/rate`) | JSONL | **Per-user** table | 5× weight preserved |
| `applied.jsonl` | JSONL | **Per-user** table | The durable "what I applied to" record — genuinely valuable, keep it relational |
| `summary.json` | JSON | **Per-user**, derived | Recompute rather than store |
| `shadow_compare.jsonl` | JSONL | Drop or per-user | Diagnostic only |
| `cv_build/` | Scratch dir | **Per-user** scratch, swept | Ephemeral by design (G8) |
| `seen_jobs.json` | JSON | Not migrated | Belongs to the manual `/apply` workflow, out of scope |

The two easy ones to underestimate are `ats_etags` (a correctness-and-cost store hiding behind a
file lock) and `applied.jsonl` (small, but it is the record a user would be angriest to lose).

### Vectors

Store as `bytea` (raw float32) on the corpus row.

**Key the embedding on a content hash, not `job_id`.** `refresh.py` re-fetches and mutates
descriptions on live postings, so a `job_id`-keyed vector silently goes stale against text it no
longer represents. Store `text_sha256` alongside the vector and re-embed when it changes. `pgvector` is only needed if similarity *search* is
ever wanted; today the vector is read whole and multiplied by a user's classifier, so a blob is
sufficient and avoids an extension dependency.

## 5. Contracts between services (what makes independent updates safe)

The embedder already solved this and the pattern is now platform-wide policy:

- Every service exposes `GET /health` returning `{ok, version, ...}` plus whatever invariants callers
  depend on (the embedder returns `model`, `dim`, `version`).
- **Callers assert compatibility and fail closed.** The embedder's callers refuse to score when the
  live backend's `(model, dim, version)` does not match the trained artifact, rather than silently
  producing vectors from the wrong space. Generalise this: a version mismatch is a 503, never a
  best-effort degraded answer.
- Queue-consuming services are **restart-safe by construction**: a job is claimed transactionally, and
  an unfinished claim expires back into the queue (`SCHEDULER_PLAN.md` §4). Restarting a worker
  mid-job costs a retry, never a lost or duplicated user-visible action.

This is what "restart one container, users keep working" actually requires. Without it, an update is a
coin flip.

## 6. Proxy ownership — strictly per-user, never pooled (decision D3, revised)

**Every user's proxy credential is used only for that user's own fetches. Credentials are never
pooled, shared, or rotated across users.** This was revised on 2026-07-26 after a pooled design was
rejected: pooling created a free-rider problem and put the platform in breach of typical residential
proxy terms of service. Both disappear entirely under strict per-user use.

### The consequence: the source tiers split

This moves the shared/per-user boundary, and the split lands cleanly on *whether a tier needs a proxy
at all*:

| Tier | Proxy? | Shared or per-user | Share of sources |
|---|---|---|---|
| Direct ATS (Greenhouse, Ashby, Lever, Workday, ...) | No | **Shared** — harvested once for everyone | ~2/3 |
| Board APIs (Remotive, Himalayas, RemoteOK, hiring.cafe, profession.hu, ...) | No | **Shared** | |
| JobSpy LinkedIn | **Yes** | **Per-user** — runs on that user's credential | ~1/3 |
| JobSpy Indeed / Google | No | **Shared** | |

So the shared corpus is still shared for everything that costs nobody anything, and the one tier that
costs real money is executed by, and paid for by, the user who wants it. Full spec:
`HARVESTER_PLAN.md` §5.

### Visibility gate — this is what makes free-riding impossible

LinkedIn-sourced rows still land in the shared corpus (so dedup and embed-once still work), but:

> **A user sees LinkedIn-sourced jobs only while they have a healthy proxy credential.**

The corpus records `source`, so this is a single filter predicate — no per-row provenance tracking,
no data partitioning. A user without a credential gets the ~2/3 of sources that cost nothing, which is
a genuinely useful product on its own, and the LinkedIn tier becomes a clear reason to add one.

### What this costs, honestly

Two users searching the same terms both fetch — there is no cross-user dedup on the proxied tier any
more. That is the deliberate price of no pooling. It is affordable because the tier is small
(LinkedIn only, after the 2026-07-26 routing change) and because it unlocks a saving that was not
available before: **the per-user LinkedIn tier can run on the user's own notification interval rather
than the platform's 15-minute harvest cycle** (`HARVESTER_PLAN.md` §5.3). A user on a 4-hour digest
does not need LinkedIn scraped 96 times a day. Estimate and then measure per-user monthly GB before
setting the default cap.

**No residual risks.** The free-rider problem and the proxy-ToS exposure that the pooled design
carried are both gone by construction.

## 7. Multi-tenant scorer

`scorer/service.py` today loads exactly one model artifact at startup. Multi-tenant needs N.

- Artifacts stored per user (`models/{user_id}/scorer.joblib`), written atomically by the trainer
  (temp + rename, `.bak` of the previous — already the existing behaviour).
- Service holds an **LRU cache of loaded models**, bounded by count and by RSS. A classifier
  (HistGBM/SVC over 768-dim vectors) is a few MB, so dozens fit comfortably; the 1.9 GB figure that
  dominates today is the *embedder*, which is no longer in this process at all.
- **Hot-swap without restart:** the trainer bumps a `model_version` on the user row; the scorer
  compares before serving and reloads on change. This removes today's "recreate scorer-service to pick
  up a retrain" step, which does not survive multi-tenancy.

## 8. Account deletion

Because every per-user row carries `user_id` with `ON DELETE CASCADE`, deletion is one statement plus
credential destruction. **The shared corpus is untouched** — it contains no personal data, only public
job postings. Design this in now: it is why no user data may ever be denormalised into a corpus row.

## 9. Decisions — resolved 2026-07-26

| # | Decision | Resolution |
|---|---|---|
| **D1** | Datastore (§4) | **Postgres for everything.** One datastore, FK cascade, JSONB for config |
| **D2** | Claude access (`COLD_START_PLAN.md` §5) | **Both supported.** API key is the recommended default; OAuth is a fully supported alternative behind an explicit cost/quota warning. A user holding both chooses per workload with a toggle |
| **D3** | Proxy (§6) | **Every user brings their own, used strictly for their own fetches — never pooled.** The LinkedIn tier becomes per-user; everything that needs no proxy stays shared |
| **D4** | Hosting | **Revised 2026-07-26: the existing $20 Contabo VPS, self-managed Postgres, everything on one box.** Supersedes the earlier "bigger VPS + managed Postgres" answer. Consequence: `OPERATIONS_PLAN.md` §7 stays **full scope** — backups, PITR, and restore drills are yours, not a provider's. See §11 for the capacity check this needs |
| **D8** | Data residency | **EU region, explicitly.** Postgres and all backups pinned to the EU. Simplest GDPR posture for the data you control; Anthropic and Telegram remain third-country processors to disclose. Confirm where the Contabo instance actually sits, and pick an **EU** backup destination |
| **D9** | Generated documents (G8) | **Ephemeral, as today.** Built, delivered, discarded; the applied-log is the durable record. No object storage, no retention policy, no user CVs persisting under your control — which also shrinks the GDPR surface |
| **D5** | Launch mode | **Invite-only.** Keeps the cohort known and scraping volume attributable, and defers the legal review from a blocker to a milestone. It is also what makes the v1 quota design sufficient — near-zero abuse surface |
| **D6** | Cutover | **Parallel copies, decommission only when proven.** The single-tenant stacks keep running; Ruben and Andras *also* get platform accounts. Compare feed quality directly on the same person before anything is switched off |
| **D7** | Frontend | **Cloudflare Pages + subdomain of `rubenlekli.com`.** Static SPA on Pages; the API reached via a Cloudflare Tunnel so the VPS needs **no open inbound ports**. Free tier covers this scale, and it reuses existing Cloudflare experience |

## 10. New risks surfaced by this design

1. **The scoring rubric is personal and currently hardcoded.** `scorer/prompts/label.md` embeds "WHO
   RUBEN IS", and its hash is `profile_version` — the key that scopes training data. Multi-tenant needs
   a **per-user rubric generated from their profile**, with a per-user `profile_version`. This is the
   single largest de-personalisation task in the whole project and it is currently invisible in every
   plan. See `COLD_START_PLAN.md` §3.
2. **Legal exposure changes shape, not just scale.** N individuals scraping for themselves becomes one
   platform scraping on their behalf. You become the responsible party. `SAAS_PLATFORM_PLAN.md` §5a
   already flags this; shared harvest makes it sharper, not softer. Get real advice before public
   signup.
3. **Postgres is now a hard single point of failure** where the per-stack model had N independent
   failure domains. Backups and restore drills are not optional.
4. **Anthropic ToS on subscription (OAuth) credentials inside a hosted service** is unverified. D2
   supports OAuth, so this now needs an actual answer rather than avoidance — check it before the
   OAuth path is offered to anyone outside the invited cohort.
5. **~~Free-riding on the proxy pool~~ — CLOSED 2026-07-26.** Eliminated by the revised D3: proxy
   credentials are strictly per-user, and the LinkedIn visibility gate (§6) means a user without a
   credential cannot consume LinkedIn results others paid for.
6. **~~Proxy-provider ToS on credential sharing~~ — CLOSED 2026-07-26.** No credential is ever used
   for another user's benefit, so the sharing/reselling prohibition typical of residential providers
   is not engaged.
7. **Per-user LinkedIn bandwidth is now an onboarding cost the user must accept.** The tier does not
   work without their own credential. Make this explicit at signup rather than a surprise, and size
   the default `daily_cap_mb` from a real measurement, not the planning estimate.
8. **`scorer/features.py` is a third personal artifact** — hardcoded core-stack and NOT-list token
   lists, coupled to the trained artifact's column semantics. Missed in every earlier draft. See
   `PROFILE_CAPTURE_PLAN.md` §6.3–6.4; it needs a single `profile_generation` counter so a profile
   edit cannot leave labels and model out of step silently.
9. **Managed Agents is beta and sits on the onboarding critical path** (`PROFILE_CAPTURE_PLAN.md`).
   Beta contracts change shape. The manual-entry fallback protects users; it does not protect the
   build schedule. Accept knowingly, and pin the beta header explicitly rather than tracking latest.
10. **Capture cost is unmeasured and it is the first thing a user spends money on.** An agentic
    interview costs materially more than a single call, on a free plan, on their own key. **Measure
    before launch, not after** — if it is high, the free plan needs a capture-attempt cap.

## 11. Capacity — the one thing D4 leaves open

D4 puts everything on the existing $20 Contabo VPS. Contabo is unusually generous on RAM for the
price, so this is plausible — but it should be **measured, not assumed**, because of what D6 stacks on
top of it.

Under D4 + D6 the box simultaneously runs:

| | Approx. resident |
|---|---|
| `job-search` single-tenant stack (kept running) | scorer-service ~1.9 GB + watcher/notifier |
| `andras-job-search` stack (kept running) | scorer-service ~1.9 GB + watcher/notifier |
| Platform embedder (TEI) | ~1.4–1.9 GB |
| Platform multi-tenant scorer | LRU of N user models (small each) |
| Postgres | shared buffers + connections for ~8 services |
| api · harvester · trainer · notifier · docgen | small, but `docgen` spawns **Chrome** — spiky |

**The 2026-07-24 incident was a memory crunch at N=2 stacks** — that is the same box, with less on it
than this. The parallel-run decision (D6) is what makes the arithmetic tight, and it is also the
decision most worth keeping.

**Do this before Phase 1 commits to the layout:** on the live VPS, record total RAM, current
`docker stats` for both existing stacks, and free disk. Compare against the table above plus the
0.87 GB steady-state corpus (§4). Three outcomes:

- **Comfortable** → proceed exactly as specced.
- **Tight** → run the platform's embedder as the *only* embedder and point the legacy stacks at it
  (`EMBEDDER_SHARING_PLAN.md` was written for precisely this), reclaiming ~1.9 GB per stack.
- **Insufficient** → revisit D4 rather than discovering it under load.

**Postgres discipline is now entirely yours.** Nightly `pg_dump` + WAL archiving to an **EU**
off-box destination, the KEK backed up separately from the database (`OPERATIONS_PLAN.md` §7 — the
trap that fails silently in both directions), and a restore drill completed before the first invited
user. None of that is optional now that no provider is doing it.
