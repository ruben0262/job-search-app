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
4c. ✅ **Done 2026-07-27. Lifted `pipeline/sources/*`** as a scripted transformation
   (`services/harvester/sources/port_from_reference.py`, committed alongside the output). Not a pure
   lift: all 17 `httpx.Client` constructions became `make_shared_client`, XML parsing moved to
   `defusedxml`, and `gather_linkedin` now requires a `ProxyLease` rather than reading a proxy from
   a config dict. The registry records each source's tier so the shared loop cannot be handed the
   proxied one.

   Two bugs found in the doing: the transformation silently dropped RemoteOK's custom User-Agent
   (the header that exists because RemoteOK blocks the generic one), and LinkedIn's proxy had no
   verified owner. Both are covered by tests now.
5. ✅ **Done 2026-07-27. Embedding vectors persist on corpus rows**, routed through `/embed/bulk`.
   T8 closed: one posting is embedded once regardless of how many users want it, asserted through a
   counting stub rather than assumed. Found and fixed a schema bug that would have failed on the
   first real embed - `embedding_version` was `String(32)` and the live version string is 36
   characters.
6. ✅ **Done 2026-07-27. T1 tenant isolation** — landed with 3b's per-user repository rather than
   later, per its own "as soon as the first per-user table exists" instruction. Two users,
   overlapping jobs, every read path returns only the caller's rows: applied log, labels, scores.
   Release blocker, now green.

**Exit status — complete 2026-07-28.** The shared harvester fills a Postgres corpus with
embeddings on a 15-minute systemd timer, and both migrated accounts run on it. First proven at
193 links / 185 new / 179 embedded with 0 failures, and idempotent on a second run (193
duplicates, 0 new); the corpus now stands at **3,896 rows, 3,892 embedded** after the reference
instances' labelled history was imported alongside live harvesting. T1 and T2 green.

Every exit condition is met: the schedule is installed and firing, and Ruben's account runs on
the platform **alongside** his single-tenant stack rather than replacing it (D6) — with Andras's
beside it, which the criteria did not ask for and which is what made the fairness split
observable.

**Exit criteria:** the shared harvester fills a Postgres corpus with embeddings, on a schedule, with
T1 + T2 green, and Ruben's account runs on it **alongside** his single-tenant stack (decision D6 —
parallel, not cutover). Compare the two feeds on the same person; decommission nothing yet. **Do not
onboard anyone else until this is true** — being your own first user is the cheapest bug-finding
available.

## Phase 2 — the per-user tail

Now it becomes a product.

**Reordered 2026-07-27: the secrets module moved from step 6 to step 0.** As written, this list had
profile capture first — but `PROFILE_CAPTURE_PLAN.md` §4.1 says capture runs on the user's own
Claude credential, and `TENANT_SAFETY.md` §5's onboarding order is `credentials → capture`. Building
capture before the envelope would mean holding a stranger's live API key with nothing behind it,
which is the exact thing §2 exists to prevent. It also unblocks the proxy credential, which the
LinkedIn loop already needs and currently has nowhere real to live.

0. ✅ **Done 2026-07-27. Secrets module** + T4, same commit. `libs/secrets`: AES-256-GCM envelope
   (KEK from the environment → per-user DEK → sealed credential), owner and kind bound in as
   associated data so a ciphertext cannot be moved between rows, KEK id recorded so rotation is a
   config change rather than a data migration, and a `Secret` type that will not stringify backed
   by a logging filter for the paths we do not own. **Open seam:** save-time validation exists as
   `mark_validated()`; the callers that make the real minimal API call arrive with capture (Claude)
   and the notifier (Telegram).
1. ✅ **Done 2026-07-27. Profile capture** (`PROFILE_CAPTURE_PLAN.md`) — `services/capture`: one
   eight-method `CaptureClient` protocol with the beta SDK confined to a single adapter,
   deny-by-default toolset and egress, approval-gated writes, turn and event caps, schema-validated
   file output. Manual entry landed in the same commit. `users.profile` refuses an unconfirmed
   profile, so the confirmation click is enforced at the write rather than by convention.
   **Outstanding:** the confirmation *form* itself is Phase 3 (no web layer yet).
2. ✅ **Done 2026-07-27. Per-user rubric rendering** + `profile_version`, **and the per-user feature
   extractor** — one workstream, as the plan insisted. `libs/prompts/label_template.md` is SACRED and
   platform-owned (calibration examples phrased relative to the candidate, so rendering stays
   deterministic); `libs/scoring/rubric.py` renders and hashes; `libs/scoring/features.py` builds the
   core / NOT-list / region-lock token sets per user with `FEATURE_NAMES` order global and
   append-only. `save_profile` renders the rubric in the same write that bumps `profile_generation`,
   so a profile without a matching rubric cannot exist.
3. ✅ **Done 2026-07-27. Multi-tenant scorer** — `services/scorer`: LRU of per-user models,
   reloaded when `users.model_version` moves rather than by restarting a container, and four
   fail-closed refusals (missing artifact row, `profile_generation` mismatch, feature-schema
   mismatch, corpus vector from a different embedder). Scores from the corpus vector, never
   re-embedding.
4. ✅ **Done 2026-07-27. Scheduler** — `libs/queue`: `SKIP LOCKED` claim, per-lane visibility
   timeouts, dead letters, round-robin fairness on `train`/`label`, and a dispatcher that advances
   `next_run_at` at enqueue so a failed delivery cannot stall a feed. T5/T6/T7 in the same commit.
   Plus `libs/queue/worker.py` and the per-lane entrypoints, so the lanes actually run.
5. ✅ **Done 2026-07-27. Trainer** — generation-scoped training set, one embedder version per fit,
   atomic artifact write, and `models` row + `model_version` bump in one transaction. BLAS thread
   caps set before numpy imports; queue concurrency of 1 alone does not fence the CPU.
6. ✅ **Done 2026-07-27. Notifier** — per-user bot token decrypted for the length of one call,
   per-user intervals, delivery dedup in the committing transaction, revoked bot terminal not
   retried, daily cap defers rather than drops.

**Exit status — complete 2026-07-27.** Every component is built, tested, and wired to a worker,
and **two real accounts run side by side on one stack**: Ruben's and Andras's, migrated verbatim
from their single-tenant instances and folded into one shared harvest with the fan-out budget
split 30/30 by round-robin — fairness working in production, where FIFO would have given the
first account all 60 queries.

Both instances' labelled history was imported into the shared corpus — **only each reference's
newest rubric**, since a label written under an older one answers a different question — and both
accounts were **retrained on platform features** rather than importing artifacts fit against the
old hardcoded token lists. Those artifacts could not have been imported: their weights are
attached to a different token list, which is exactly the mismatch the scorer refuses.

```
                    Ruben              Andras
labels @ gen 1      2,529              945
model               v1, servable       v1, servable
scored              3,892              3,892
high band           345                64
overlap of top-100 feeds: 0
```

Zero overlap on the same corpus is the proof the shared design works: one posting stored,
deduplicated and embedded once, ranked disjointly by two per-user models.

**Three silent bugs surfaced by doing it on real profiles**, all fixed: a hyphenated skill never
matched its spaced spelling (`full-stack` matched nothing in "Full Stack Developer", and the
feature simply read 0); splitting a compound produced fragments that fire on unrelated text
(`Spring Boot` yielded `boot`, matching *bootstrap*); and `remote_policy: any` dropped the
location gate entirely, which would have shown a candidate roles requiring relocation anywhere
on earth.

**Phase 2 is complete.** What remains for these two accounts is credentials — a login password and a
Telegram bot token each — which only their owners can supply. Everything those gate is built.

**Exit criteria:** two accounts (Ruben + Andras) run side by side on one stack, each with their own
model, feed, and schedule. This is the real proof, and it replaces the two separate deployments that
prompted this whole design.

## Phase 3 — self-service

In onboarding order, which is also dependency order.

**Backend first; the web layer is step 12, last** (decided 2026-07-28). Every step below is built
as API endpoints with tests, and the React that drives them comes at the end in one piece. Two
reasons: a form is cheap to write against a settled API and expensive to rewrite against a moving
one, and an endpoint with a test proves a rule holds — a form only proves it holds when somebody
clicks in the expected order. Where a step below says "form", read "the endpoints a form will
call, and the validation behind them".

It stays *inside* Phase 3 rather than becoming a later phase, because this phase's exit criterion
is a non-technical person signing up unaided — which is not testable without it — and Phase 4
onboards a cohort who would have nothing to use.

**Reordered 2026-07-28 — see `ONBOARDING_AND_PROCESSING.md`.** Onboarding no longer calls an LLM at
all: it collects identity, documents, history, credentials and config, and nothing else. Capture
becomes a *dashboard action*. That makes onboarding immune to a provider outage, free to the user,
and finishable in one sitting.

1a. ✅ **Done 2026-07-27. Auth primitives** — `libs/auth`: argon2id at FlowSolutions' production
   parameters, email tokens stored as hashes only, refresh sessions hashed and rotated with family
   revocation on replay. Password minimum raised 8 → 12 per `AUTH_REUSE.md` §4.
1b. ✅ **Done 2026-07-27. The `api` service** — signup, email confirmation, login, refresh, logout,
   the §3 cookie policy (refresh cookie scoped to its own path), no account-existence oracle in
   either the response or the timing, and a process-local rate limiter carrying §4.1's caveat
   intact. **Transactional email** is Gmail SMTP with an app password, capped at 400/24h against
   Gmail's ~500 — fine for an invited cohort, revisit before self-serve.
2. ✅ **Done 2026-07-28. `plans` table with the single `free` row**; quotas resolved from it rather
   than from code constants, with a per-user override for support grants. The seam Stripe attaches
   to later — adding a paid tier is an INSERT.
3. ✅ **Done 2026-07-28. Credential entry, validated at save.** Both Claude kinds satisfy the
   requirement (D2); the **Telegram chat id is captured automatically** rather than asked for; an
   unreachable provider stores the credential unvalidated rather than blocking onboarding.
4. ✅ **Done 2026-07-28. Config schema v2 — sections.** Per-platform search sections with an `all`
   default, replacing the flat keyword list. The keyword cap spans sections so five sections cannot
   buy five times the fan-out budget. Stored v1 rows still read correctly.
5. ✅ **Done 2026-07-28. Document upload + server-side text extraction.** What "process data"
   consumes. **Extracted text is stored; the original file never touches disk** — nothing downstream
   reads a PDF, so keeping strangers' records would be a liability with no consumer.
6. ✅ **Done 2026-07-28. Onboarding flow** — the seven pages of `ONBOARDING_AND_PROCESSING.md` §2,
   all required, no LLM. **Completion is derived from the rows each page writes, never stored**, so
   revoking a credential later reopens that page rather than leaving an account marked finished and
   unable to deliver. `/complete` deliberately does not set `users.active`: a notify loop with no
   trained model to rank with fails every cycle identically. The config form carries a **live match
   count** — approximate, and it says so, but it catches the misspelled keyword that would
   otherwise surface as an empty feed a day later.
7. ✅ **Done 2026-07-28. Process data** — one plain Messages call on the user's own credential, in
   its own `process` queue lane. Not a Managed Agents session: text is extracted server-side at
   upload, so there was nothing left to mount — and dropping the sandbox is what made **OAuth
   viable**, since Managed Agents is API-key-only and would have silently required every user to buy
   credit, contradicting D2. The output is a **draft** on `users.profile_draft`; confirming it is
   the only path to a live profile, because every `not_list` entry becomes a hard gate. The warning
   names the number of labels it will invalidate rather than asking "are you sure?".
8. ✅ **Done 2026-07-28. The relabeller** — the `label` lane worker. Without it a re-process leaves
   zero usable labels at the new generation and training cannot run at all, so the account degrades
   to *no model* rather than to a stale one. **One queue job is one batch of 50**, because the
   worker commits the handler's effect and the job's completion together — so the queue job's size
   is the unit of work a crash can destroy, and at 50 that is one Haiku call rather than four. The
   worker re-enqueues while stale labels remain, which lets round-robin interleave two users'
   relabels instead of running one to completion. Confirming a profile queues the first batch
   rather than leaving it to a second button. The most expensive step on the platform, which is
   why process→retrain is enforced and retrain→process deliberately is not.
9. **Cold-start pipeline**: pool selection → labelling → first train, with visible stage
   progression (`COLD_START_PLAN.md`). One wider recency pass here, since the steady-state 24-hour
   window shows a new account almost nothing.
10. **Dashboard** — **state-driven**: reads the counters and says whether data needs processing, jobs
   need relabelling, or the model needs training, rather than offering buttons and hoping they are
   pressed in the right order. Plus feed stats, model status, quota usage, the **health banners**
   from `OPERATIONS_PLAN.md` §4b, and an edit page for everything onboarding collected.
11. **The GDPR obligations the cleared legal review depends on** (added 2026-07-27). The review
   passed; these are the artifacts that make it true in the product, and none exist yet:
   - **Privacy policy naming each processor** — Anthropic, Telegram, the proxy provider. Linked
     from signup, not buried.
   - **Explicit consent before document text is sent to Anthropic.** Reshaped 2026-07-28: the
     obligation was written when capture uploaded files to the Anthropic Files API, and it named
     upload as the moment. Files no longer leave the platform — text is extracted server-side — so
     the moment consent is owed is **process data** (step 7), not upload. Same commit as that step.
   - **Deletion that reaches outside Postgres.** Largely dissolved by the same change: with no Files
     API uploads there is nothing outside the database to orphan, and the cascade is provably
     complete. What remains is to **verify** that — an assertion that account deletion leaves no row
     anywhere — rather than to build a second deletion path.

12. **The web layer** — React on Cloudflare Pages behind the Tunnel (D7). The seven onboarding
   pages, the dashboard, and the edit pages, with **every form generated from the shared JSON
   Schema** rather than hand-written (`CODE_STRUCTURE.md` §8) so a field cannot exist on one side
   and not the other. Built last, against endpoints that have stopped moving.

**Exit criteria:** a non-technical person signs up unaided and gets a working feed. Test this with
a real person who has not seen the product, and watch without helping.


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
