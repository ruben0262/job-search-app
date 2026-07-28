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
9. ✅ **Done 2026-07-28. Cold-start pipeline**: pool selection → labelling → first train
   (`COLD_START_PLAN.md`). **Stage 0 is a rules-only score** from the config alone
   (`libs/scoring/rules.py`) — free, instant, and crude, but it turns a 30-minute cold start into a
   30-second first impression, and an empty page is what people close. **Stage 1 is deliberately
   not built**: a bootstrap model on other users' labels is a consent question, and one on the
   operator's labels alone is a separate artifact to version for a gap stage 0 already covers.
   The pool is banded 40/40/20 by that same rules score and drawn by **striding**, because taking
   the head of the ranking buys 120 near-identical jobs — a lot of money for one job's worth of
   information. **The `label` lane has one job kind and decides for itself** whether the account
   needs a relabel or a cold start, then queues the train: a rule that has to hold everywhere is
   safer in one function than re-derived by every caller. A partial cold start still trains at 100
   labels — 280 labels and no model because 20 calls failed is the worst outcome available.
10. ✅ **Done 2026-07-28 (backend). Dashboard** — **state-driven**: `GET /dashboard` reads the
   counters and says whether data needs processing, jobs need relabelling, or the model needs
   training, rather than offering buttons and hoping they are pressed in the right order. Plus feed
   stats, model status, quota usage, and the **health banners** from `OPERATIONS_PLAN.md` §4b.
   `GET /feed` is **stage-aware** and says so: keyword-ranked until a servable model exists, and a
   feed that silently changed how it ranked would be one nobody could reason about. The one design
   correction found by testing: it branches on the stored `onboarding_completed_at`, **not** on
   live completeness — a revoked credential must surface as a banner, not send a working account
   back to the signup wizard where nothing explains what broke.
11. ✅ **Done 2026-07-28. The GDPR obligations the cleared legal review depends on.** All three are
   behaviour now, not claims in a document:
   - **`GET /legal/privacy` names each processor** — Anthropic, Telegram, the proxy provider, with
     what each receives and whose credential pays. Public, so signup can link it. Served from
     `libs/legal` so it cannot drift from the consent screen; a test asserts they are one object.
   - **Consent before document text reaches Anthropic**, at *process data* rather than at upload —
     files no longer leave this server, so that is the moment it is owed. `/process` returns 403
     without it. **Version-compared**, so naming a new processor re-asks everybody by construction
     rather than by anyone remembering.
   - **Deletion that reaches everything, proved rather than asserted.** The test enumerates every
     table in the metadata carrying a `user_id` and asserts none survives — so adding an eleventh
     table and forgetting its cascade fails there instead of leaving a stranger's rows behind. The
     DEK is crypto-shredded in the same request, because a backup restore that resurrected a
     deleted user's Claude key is the worst outcome a delete request could have.

12. ✅ **Done 2026-07-28. The web layer** — React + TypeScript on Vite, ready for Cloudflare Pages
   behind the Tunnel (D7). The seven onboarding pages, the dashboard, the feed, the profile
   confirmation, settings and the public privacy page. **`openapi.json` and the generated types are
   committed and CI diffs them**, so changing a response shape without regenerating fails the build
   rather than surfacing as a runtime `undefined` weeks later. Two runtime dependencies and a 69 kB
   gzipped bundle: no router (a wizard plus four screens does not need one) and no component
   library. **Nothing in it re-derives a server rule** — which onboarding page you are on and what
   your account needs next both come from the API, because a second copy of those rules is the one
   that goes stale.

**Phase 3 complete 2026-07-28** — all twelve steps. What remains before the first invited
user is Phase 4's operations work, not more product.

**Exit criteria:** a non-technical person signs up unaided and gets a working feed. Test this with
a real person who has not seen the product, and watch without helping.


## Phase 4 — first cohort (invite-only, decision D5)

- ✅ **The `score` lane — added 2026-07-28**, closing a gap that would have frozen every feed.
  Nothing turned a harvested row into a score; the harvest cycle now enqueues one job per active
  account and `services/scorer` drains it. A lane rather than a call from the notifier because the
  scorer's fail-closed checks belong in the deployable that owns them. A missing model ends the job
  quietly — that is the normal state of a new signup, not a failure.
- ✅ **Monitoring, alerts and the admin view — done 2026-07-28.** `libs/ops` turns the platform
  into numbers and decides which need a person; `GET /admin/health` serves them and
  `deploy/scripts/check_health.py` runs on a 15-minute timer. **The exit code is the interface** —
  0/1/2 — so a broken mail configuration cannot make the monitoring itself silent, which is the
  failure this whole section exists to prevent. Admin is `ADMIN_EMAILS`, not a column: granting it
  is a deploy, and a boolean would be one UPDATE away from privilege escalation audited in the
  database it grants access to. Non-admins get **404, not 403**.
- ✅ **Backup with a completed restore drill — done 2026-07-28.** Nightly `pg_dump`, verified as it
  is written, 30-day retention, **and the KEK deliberately outside it** — `backup.service` cannot
  even read the key, which is the surest way to keep a dump and its key apart.
  `restore_drill.py` **passed against a real 20 MB backup**: every row count matched, a stored
  credential decrypted with the separately-held KEK, and both models came back servable. It found a
  bug doing it, which is the argument for having a drill at all.
- ⚠️ **Transactional email is not configured — deferred by decision, 2026-07-28.** `libs/mail` is
  built and tested, but with no `SMTP_*` in `deploy/.env` the API resolves to `ConsoleSender`:
  signup confirmations and health alerts are *written to a log instead of sent*, and nothing about
  that looks broken from the outside.

  **The consequence, stated so nobody rediscovers it the hard way:** the two migrated accounts are
  unaffected — they were created directly and are already verified — but **no new account can
  complete signup**, because the confirmation link never arrives. Anyone who tries will see a
  working signup form and then nothing. So this is not a blocker for the current two users and *is*
  a hard blocker for the invited cohort, and it must be closed before anybody is invited.
  **Off-box backup storage is deferred by decision, 2026-07-28** — there is nowhere to put it yet.
  The dumps therefore cover a bad migration, a dropped table and an operator mistake, and do not
  cover disk or host loss. Written down rather than left implied, because an accepted risk nobody
  recorded is one somebody later mistakes for an oversight.
- ✅ **Every service containerised — done 2026-07-28.** `api`, `notifier`, `scorer`, `trainer` and
  `cloudflared`, behind a `full` compose profile so a bare `up -d` never starts delivery by
  accident. The API publishes **no port**: the tunnel is the only way in (D7).
  A **`service-deps` CI matrix** installs each service's requirements alone and imports its
  entrypoint — written after the same missing-dependency bug shipped three times, and it caught the
  third within a minute of existing.
- **Measure capture cost first** (`PLATFORM_ARCHITECTURE.md` §10.10) — it is the first spend a
  user sees, on a free plan, on their own key.
- Onboard 3–5 **invited** users. Quotas on from day one.
- Andras gets a platform account alongside his existing stack (D6); still decommission nothing.
- Watch: harvest budget consumption, proxy GB, trainer queue depth, embedder queue depth, Postgres
  size vs the projection in `PLATFORM_ARCHITECTURE.md` §4.
- Legal review before signup opens beyond invites — with D5, this is a milestone, not a blocker.
- **Decommission the single-tenant stacks only once the platform demonstrably matches them.**

**Exit status 2026-07-28.** Operations is done and proven rather than asserted: monitoring and
alerts (`libs/ops`), a read-only admin view, and nightly backups whose **restore drill has passed
twice** — the second time including that the artifact files behind the model rows actually resolve,
a check added because the first version would have blessed a restore that could not score.

Every service is containerised and the API is **live at `mahoraga-app.rubenlekli.com`** through the
tunnel, with port 22 the only thing listening publicly on the box. The `score` lane closed a gap
that had frozen both feeds silently.

**Not done, and both deliberate:** transactional email (above) and the first invited cohort, which
depends on it. `notifier` and `trainer` are built and not started — starting the notifier messages
real people, and that is a decision rather than a deployment step.

## Phase 5 — the generation and feedback half (added 2026-07-28)

Everything so far *finds and ranks* jobs. Neither half of what a user does with one exists: they
cannot act on a match in a way the platform learns from, and they cannot produce the document the
match is for. The `docgen` lane has existed since Phase 1 with **no worker behind it** — the same
shape of gap the `score` lane turned out to be.

### 5a — the feedback loop ✅ **done 2026-07-28**

**This is the half that makes the product get better, and without it the model is frozen at whatever
Haiku thought.** `user_labels.source = 'feedback'` at weight 5 already exists in the schema and
nothing writes it.

- **Buttons on the digest.** Apply / Skip / Build CV, as inline keyboard callbacks.
- **A webhook, not polling.** The API is publicly reachable through the tunnel now, so Telegram can
  post to it — one request per tap instead of a process asking "anything yet?" forever.
- **Two secrets, because one is not enough.** A per-user path secret so a tap is attributable to an
  account without trusting the body, and Telegram's own `secret_token` header so a guessed path is
  still refused. Neither alone is sufficient: the path leaks in logs, and the header does not say
  *which* user.
- **Only the account owner's taps count.** A channel has other members; a stranger tapping Apply
  must not teach somebody else's model. Carried from the reference instance, which learned this.
- **Debounced retrain**, per `SCHEDULER_PLAN.md` §5: a burst of twenty taps is one retrain, ten
  minutes after the last.

**Live.** Both accounts' bots are registered against `mahoraga-app.rubenlekli.com`, Telegram reports
no pending updates and no delivery error, and a request to the endpoint with a wrong secret gets a
404 from the public internet. The digest changed shape to carry the buttons — see the changelog for
why that reversed an earlier decision.

### 5b — docgen

The CV and cover-letter builder, ported from `pipeline/cv_builder.py` (~993 lines, Chrome for PDF).
**The only image with Chrome** (`CODE_STRUCTURE.md` §5), slow (~90–130 s), which is exactly why it
has its own lane and its own container.

Generated documents stay **ephemeral** (D9): built, delivered, discarded. The applied-log is the
durable record. That is not only tidiness — it is what keeps strangers' CVs off this disk and the
GDPR surface small.

**Started 2026-07-28.** `tailor_cv.md` and `tailor_cover.md` are ported byte-identical into
`libs/prompts` — they are SACRED, and generalising during a copy would make the diff against the
original unreviewable. **Neither is usable yet:** between them they name the reference instance's
owner 28 times, exactly as `label_template.md` did before Phase 2 gave it `<<NAME>>`, `<<WHO>>` and
`<<HARD_GATES>>`. A test refuses to let either be wired into docgen until the same is done, because
the alternative is a user receiving a CV written about somebody else.

**What remains, in order:** generalise the two prompts; generalise `base_cv_dev.html` and
`base_cv_quant.html` into 2–3 platform templates filled from the profile (§6.5 — buy, do not
generate); then the worker, Chrome, and delivery.

**Exit criteria:** a user taps Apply on a job, their model learns from it, and a retrain uses it.
A user taps Build CV and receives a tailored PDF that names nothing the facts block does not
support.

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
