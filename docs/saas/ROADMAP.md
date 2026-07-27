# Roadmap — simple version shipped fast, then improved

**Status:** design, 2026-07-26. The brief: plan a simple version, implement, test, get it to users
ASAP, improve later. This orders the work so that each phase ends with something usable and nothing
is built before the thing it depends on.

---

## Phase 0 — decided 2026-07-26 ✅

- **D1 Postgres for everything.** One datastore; FK cascade; JSONB for config.
- **D2 Both Claude credential types**, user chooses. API first and preselected; OAuth is a second
  adapter behind the same interface, built after — not alongside.
- **D3 (revised) Every user brings their own proxy, strictly per-user, never pooled.** The LinkedIn
  tier runs per-user on their own credential; everything proxy-free stays shared
  (`HARVESTER_PLAN.md` §5).

- **D4 (revised) Everything on the existing $20 Contabo VPS, self-managed Postgres.** Backups, PITR,
  and restore drills stay yours — `OPERATIONS_PLAN.md` §7 is full scope. Needs the capacity check in
  `PLATFORM_ARCHITECTURE.md` §11 before Phase 1 locks the layout.
- **D8 EU data residency**, explicitly — Postgres and backups both.
- **D9 Generated documents stay ephemeral** (G8 closed): built, delivered, discarded.
- **D5 Invite-only at launch.** Legal review becomes a milestone, not a blocker, and the near-zero
  abuse surface is what makes the v1 quota design sufficient.
- **D6 Parallel copies, decommission only when proven.** Single-tenant stacks keep running; Ruben and
  Andras *also* get platform accounts, so the two can be compared on the same person.
- **D7 Cloudflare Pages + subdomain of `rubenlekli.com`**, API via a Cloudflare Tunnel so the VPS
  needs no open inbound ports.

One follow-up, not blocking Phase 1: verify Anthropic's terms on subscription credentials in a hosted
service (gates offering OAuth beyond the invited cohort).

Also fold in from `GAPS_REVIEW.md` — cheap now, expensive later:
- **G5 Alembic migrations** from the first table.
- **G7 `users.timezone`** (IANA) and `TIMESTAMPTZ` everywhere; per-day counters reset at the user's
  local midnight.

## Phase 1 — the shared core (largest, least visible)

The unglamorous half. Nothing user-facing ships here, and skipping ahead of it means building the
product twice.

0a. ✅ **Done 2026-07-26.** Repo laid out per `CODE_STRUCTURE.md`, `import-linter` wired into CI on
   the first commit, `embedder/` → `services/embedder/` as its own commit with 15 passed / 9
   skipped identical either side. Boundaries are trivial to establish now and painful to retrofit
   once eight services import each other, which is why this went first.
0b. ✅ **Done 2026-07-26 — D4 confirmed, verdict comfortable** (`PLATFORM_ARCHITECTURE.md` §11):
   23 GiB RAM with 22 GiB available, 8 vCPU, 382 GB free disk, ~337 MiB currently resident. Roughly
   3× headroom. **But the headroom is partly an artifact of what is *not* on the box:** neither
   single-tenant stack nor the deployed embedder runs there, so the D6 parallel-run arithmetic that
   made §11 tight has not actually been paid yet. Three follow-ups fall out of it and belong in
   this phase — stand the embedder up on the target box, note that there is no swap (a spike
   OOM-kills rather than degrades, and `docgen` runs Chrome), and install Postgres with its backup
   and restore drill before any schema.
0c. ✅ **Done 2026-07-27.** Platform Postgres 17 deployed (`deploy/compose.yml`) and the platform's
   own embedder brought up on the same box — gates 1, 2, 4, 5 green (23 passed / 1 skipped, 832 MiB
   against the 2 GB budget). The legacy embedder on the other machine is untouched, per D6.
1. ✅ **Done 2026-07-27. Postgres schema**: `users`, `user_config`, `user_secrets`, `corpus`,
   `user_job_state`, `user_labels`, `jobs` (queue), `models`. Alembic from the first table (G5),
   `TIMESTAMPTZ` throughout (G7), applied and drift-checked against the live database.
2. ✅ **Done 2026-07-27. T2 dedup regression suite**, written against today's behaviour before
   anything was touched. 66 tests: golden cases whose expected values were produced by *executing*
   the reference (not by reading its rules), the same rules restated as intent, and a differential
   fuzz of 40,000 seeded random inputs with **0 divergences**.
3. ✅ **Done 2026-07-27. Ported `pipeline/lib/store.py` → Postgres corpus repository.** The task was
   flagged here as the critical path — 578 lines, dedup semantics load-bearing — and splitting it in
   two is what made it tractable: the dedup **key functions** (pure, pinned by T2 in step 2) and the
   **storage layer** (`libs/corpus/repository.py`, 14 behaviour tests). T2 stayed green throughout,
   which was the entire point of writing it first.

   The port surfaced a schema error in step 1's migration: `corpus.canonical_url` had been made
   unique and `NOT NULL`. The reference keys `all_links` on **`url`** and leaves both derived keys
   *indexed but not unique and nullable* — dedup is a query performed **before** insert, not a
   constraint, and the key functions return `None` for input they cannot use. Corrected in migration
   `fe23e709fd46`.
3b. ✅ **Done 2026-07-27. Ported the other stores** — `ats_tokens`, `ats_etags` (**file lock
   replaced by row-level upsert**), `applied`, `labels`, `feedback`. `feedback` turned out not to be
   a separate store at all: it is a label with `source='feedback'` and weight 5, so the train-time
   row-duplication blend becomes a query.

   Reading the reference for this step also exposed a **dedup layer missing from step 3**: the
   resolved Apply URL (`processed.app_canonical`). Without it a LinkedIn posting and the Greenhouse
   row its Apply button resolves to are two rows in every feed. Added as a fourth layer, checked in
   both directions. Migration `fccd4d3a84ff`.
4a. ✅ **Done 2026-07-27.** **The harvester's governor, before the harvester.** Bounded config schema
   (`libs/contracts/config.py`), query-set folding, fan-out budget with round-robin selection
   (`services/harvester/query_set.py`), `proxy_credentials`, the LinkedIn visibility gate, and the
   tier's global controls (`services/harvester/governor.py`). Built before the scraper deliberately:
   the budget is what stops an unbounded harvest.

   Execution of the per-user LinkedIn loop is **not** wired - it is an ordinary queue job and
   `libs/queue` is Phase 2. Nothing half-built runs.
4b. ✅ **Done 2026-07-27. The fetch layer.** `services/harvester/fetch.py` makes D3 structural: one
   client factory, per-user leases, LinkedIn-only routing, `trust_env=False`, metered bytes, and
   quarantine on consecutive failures. T13 is closed by it. `make_client()` is the only HTTP-client
   factory, which is what keeps "this user's credential, for this user's fetch only" auditable rather
   than hopeful.
4c. **Lift `pipeline/sources/*`** — the ~3,551-line adapter port. Mechanical, but needs live
   endpoints to verify, so it is its own step.
   HTTP-client factory, which is what keeps "this user's credential, for this user's fetch only"
   auditable rather than hopeful.
5. **Persist embedding vectors on corpus rows**; route harvest embedding through `/embed/bulk`.
6. ✅ **Done 2026-07-27. T1 tenant isolation** — landed with 3b's per-user repository rather than
   later, per its own "as soon as the first per-user table exists" instruction. Two users,
   overlapping jobs, every read path returns only the caller's rows: applied log, labels, scores.
   Release blocker, now green.

**Exit criteria:** the shared harvester fills a Postgres corpus with embeddings, on a schedule, with
T1 + T2 green, and Ruben's account runs on it **alongside** his single-tenant stack (decision D6 —
parallel, not cutover). Compare the two feeds on the same person; decommission nothing yet. **Do not
onboard anyone else until this is true** — being your own first user is the cheapest bug-finding
available.

## Phase 2 — the per-user tail

Now it becomes a product.

0. **Profile capture** (`PROFILE_CAPTURE_PLAN.md`) — Managed Agents session, confirmation,
   deterministic render. Blocks step 1: the rubric renderer has no input without it.
1. **Per-user rubric rendering** + `profile_version` (`COLD_START_PLAN.md` §3, `PROFILE_CAPTURE_PLAN.md`
   §6.1). Flagged as the largest hidden task; give it real time.
1b. **Per-user feature extractor** (`PROFILE_CAPTURE_PLAN.md` §6.3) and the single
   `profile_generation` counter that keeps labels and model artifacts in step (§6.4). Same
   workstream as the rubric — do not schedule them apart.
2. **Multi-tenant scorer** with an LRU of user models and `model_version` hot-swap.
3. **Scheduler** — the four-lane Postgres queue, with T5/T6/T7 in the same commit.
4. **Trainer** — concurrency 1, CPU-fenced, debounced.
5. **Notifier** — per-user bot tokens, per-user intervals, delivery dedup.
6. **Secrets module** + T4, same commit.

**Exit criteria:** two accounts (Ruben + Andras) run side by side on one stack, each with their own
model, feed, and schedule. This is the real proof, and it replaces the two separate deployments that
prompted this whole design.

## Phase 3 — self-service

In onboarding order, which is also dependency order:

1. **Signup, email confirmation, login** (ported per `AUTH_REUSE.md`) + **transactional email**, which
   is now load-bearing rather than optional (`OPERATIONS_PLAN.md` §6).
2. **`plans` table with the single `free` row**; quotas resolved from it, not from code constants.
3. **Credential entry, validated at save** — before capture, since capture spends the user's key.
4. **Profile capture** (`PROFILE_CAPTURE_PLAN.md`): Managed Agents session, confirmation form,
   deterministic render. Manual-entry fallback in the same commit — capture must never hard-block.
5. **Config form** generated from the shared schema, with the live match count.
6. **Telegram connect** with automatic chat-ID capture.
7. **Cold-start pipeline**: pool selection → labelling → first train, with visible stage progression.
8. **Dashboard**: feed stats, model status, quota usage, and the **health banners** from
   `OPERATIONS_PLAN.md` §4b.
9. **The GDPR obligations the cleared legal review depends on** (added 2026-07-27). The review
   passed; these are the artifacts that make it true in the product, and none exist yet:
   - **Privacy policy naming each processor** — Anthropic, Telegram, the proxy provider. Linked
     from signup, not buried.
   - **Explicit consent at CV upload**, taken at the moment the file leaves for the Anthropic Files
     API rather than as a blanket checkbox at signup. Same commit as profile capture (step 4).
   - **Deletion that reaches outside Postgres.** The cascade is provably complete for the database
     and silent about everything else; account deletion must also delete files uploaded to the
     Anthropic Files API. Build it with capture, not after — an orphaned upload nobody knows about
     is the hardest kind to find later.

**Exit criteria:** a non-technical person signs up unaided and gets a working feed. Test this with a
real person who has not seen the product, and watch without helping.

## Phase 4 — first cohort (invite-only, decision D5)

- **Operations first** (`OPERATIONS_PLAN.md`): monitoring, alerts, admin view, transactional email,
  backup with a **completed restore drill**. Onboarding people before this means their failures are
  invisible to everyone including them.
- **Measure capture cost first** (`PLATFORM_ARCHITECTURE.md` §10.10) — it is the first spend a
  user sees, on a free plan, on their own key.
- Onboard 3–5 **invited** users. Quotas on from day one.
- Andras gets a platform account alongside his existing stack (D6); still decommission nothing.
- Watch: harvest budget consumption, proxy GB, trainer queue depth, embedder queue depth, Postgres
  size vs the projection in `PLATFORM_ARCHITECTURE.md` §4.
- Legal review before signup opens beyond invites — with D5, this is a milestone, not a blocker.
- **Decommission the single-tenant stacks only once the platform demonstrably matches them.**

## Deliberately deferred

Do not build these before Phase 4, regardless of how appealing they look mid-build.

| Item | Why later |
|---|---|
| Query subsumption in the harvester | Result caps make it unsafe; exact dedup captures most of the win (`HARVESTER_PLAN.md` §2) |
| Redis | Postgres `SKIP LOCKED` is sufficient below ~100 users |
| pgvector | Nothing does similarity *search* yet |
| Non-Claude labelling models | Needs a relabel A/B per `MODEL_SELECTION_RESEARCH.md` §8; the ladder is Haiku+batch+cache first |
| Billing / payments | Business decision, not a technical blocker |
| Shared bootstrap model trained on all users' labels | Consent surface; launch with operator-only labels (`COLD_START_PLAN.md` §2) |
| Postgres row-level security | Second layer; get the repository layer right first |

## Sequencing risks

1. **The `store.py` port is the critical path** and the easiest to underestimate. Dedup semantics are
   tuned (canonical folding, location-aware content keys) and quietly load-bearing. T2 first is not
   optional.
2. **The per-user rubric AND feature extractor are one workstream, not two tasks.** Together they
   gate every user's scoring quality, they inherit the generation prompts' change-control rules, and
   splitting them risks labels and model artifacts drifting out of step (`PROFILE_CAPTURE_PLAN.md`
   §6.4).
4. **Managed Agents is beta and sits on the onboarding path.** Pin the beta header; expect one
   contract change during the build.
3. **Being your own first user (Phase 1 exit) is the cheapest bug-finding available.** Resist
   onboarding a second person early.
