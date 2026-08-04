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
   revocation on replay. Password minimum raised 8 → 12 per `AUTH_REUSE.md` §4, then lowered to 9 on
   2026-07-31 (owner's decision; see that section).
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
- ✅ **Transactional email — live 2026-07-28.** Sending as `no-reply@flow-solutions.xyz` through
  Zoho's **EU** host, the same mailbox the other FlowSolutions services use, so SPF and DKIM for the
  domain are already correct. Verified by sending a real message, not by configuration alone.

  The hour this could have cost, written down: `smtp.zoho.com` answers `535 Authentication Failed`
  for this account because it is on the EU region — an error indistinguishable from a wrong
  password. `smtp.zoho.eu` authenticates immediately.

**Exit status 2026-07-28.** Operations is done and proven rather than asserted: monitoring and
alerts (`libs/ops`), a read-only admin view, and nightly backups whose **restore drill has passed
twice** — the second time including that the artifact files behind the model rows actually resolve,
a check added because the first version would have blessed a restore that could not score.

Every service is containerised and the API is **live at `mahoraga-app.rubenlekli.com`** through the
tunnel, with port 22 the only thing listening publicly on the box. The `score` lane closed a gap
that had frozen both feeds silently.

~~**Not done, and both deliberate:** transactional email and the first invited cohort.~~
**Superseded 2026-07-31.** Email is live and proven by real sends. `notifier` and `trainer` are
started — the notifier has delivered **153 digests** and the trainer has produced models for both
accounts. Phase 4 is closed; the first *external* invited cohort is a business decision, not an
engineering one.

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

**Built 2026-07-28, not yet deployed.** One template, `libs/templates/base_cv.html`, filled from
`ProfileSchema` by `libs/templates/cv.py` (§6.5 — buy, do not generate); both prompts generalised;
`services/docgen` is the worker — one Claude call, Chrome for the PDF, a Telegram upload, and a
scratch directory removed in a `finally`.

The mechanical guarantees are in the changelog; the one worth repeating here is that **a CV with
hidden content is not delivered at all.** Invisible keyword stuffing looks perfect and gets the
person filtered out by the system it was aimed at.

~~**What remains:** build the image on the box, start it under the `full` profile, and watch one
real tap through.~~ **Deployed and exercised 2026-07-31:** `docgen` is running under the `full`
profile and **three CVs have been built end to end**. `libs/templates/cv.py` was deleted the same
day — a real build reads the user's own `base_cv.html`, which the schema renderer could never have
produced.

Cover letters are still deliberately not wired — `tailor_cover.md` is generalised and ready, and
nothing yet says *when* a posting wants one.

**Exit criteria, honestly scored:**

- *"A user taps Build CV and receives a tailored PDF"* — ✅ **met.** Three docgen jobs done.
- *"A user taps Apply, their model learns from it, and a retrain uses it"* — ❌ **not met.**
  Production holds 3,499 `llm` labels and **zero `feedback` labels**. The path exists
  (`libs/feedback/actions.record` → `user_labels` at weight 5, called by the Telegram webhook) and
  is tested; it has simply never fired. The 98 `applied_at` rows on the founder account were
  imported from the reference instance and never went through it.

## Phase 6 — make the loop turn (added 2026-07-31)

Phases 1–5 built a platform that finds, ranks, delivers and writes. What it does **not** yet do is
learn from what the user does with a job — and that is the whole premise. This phase is short and
ordered by what blocks what.

### 6a — finish the re-embed, then retrain ✅ **done 2026-07-31**

The corpus moved from embedding `description` alone to `title_desc`, invalidating every existing
vector by design. **All 6,601 rows re-embedded**, both accounts retrained on the new space, and the
first delivery through it verified end to end (*Backend Engineer (Python/AWS)*, score 90, posted
12:47 UTC and sent 13:47).

The owner's model: **svc**, 2,554 labels, CV-AP 0.663, cutoff **38 at 65% recall** — near the
reference instance's 44, replacing a Ridge head reading a cutoff of 60 that was never tuned.

Six bugs stood between the two, each hidden by the one in front of it: an hour-long embed
transaction that committed nothing, a deadlock against the harvest cycle that discarded it, a raw
`httpx` timeout escaping past every `EmbedderError` handler, contention mistaken for a broken
gateway, a retrain guard that only counted labels and had no concept of the vector space moving,
and a cutoff sweep that fell back to the label threshold. All fixed and changelogged.

**Still open from this thread:** Andras's model kept the conservative best-F1 fallback cutoff (60 at
37% recall) — his next natural retrain picks up the corrected logic. And the harvest timers cannot
be paused for a rebuild without a sudo password, which is why the rebuild was made contention-proof
instead.

### 6f — the account surface, page by page (added 2026-07-31)

Walked with the owner. Ticked off: **Jobs**, **Your details**, **Settings**, **Status**,
**Notifications**, and the auth flows.

- **Notifications** ✅ — per-item dismiss, mark-all-read (eye) and delete-all (bin), with a real
  `DELETE`-shaped endpoint rather than a hidden flag.
- **Status** ✅ — and it gained the check it was missing. The page was **all green through the
  six-hour delivery outage on 2026-07-31**: collection fine, scoring fine, bot and channel fine,
  notifier completing its jobs and sending nothing. A `delivery` component now reports the
  conjunction that actually means "stalled" — matches waiting *and* nothing sent — because either
  fact alone is normal.
- **Your details** ✅ — the Telegram **channel is now required**, in onboarding as well as settings,
  and the chat and channel IDs are displayed. They are addresses, not secrets, and hiding them only
  stopped a user confirming where their jobs go.
- **Settings** ✅ — delivery pause verified working end to end: the toggle writes `user.active` and
  `dispatch_due_notifications` filters on it, so a pause stops jobs being enqueued at source rather
  than dropping them later. On/off state is shown in the card and as a dashboard banner.
- **Auth** ✅ — signup, login, change password and email verification are all covered and passing
  (`test_api_auth.py`, `test_auth.py`, `test_gdpr.py`), verified against a real Postgres:
  **1,034 passed, 11 skipped**.

**Remaining pages: Profile, Model, Platform.** The most concrete gap found while walking these:
**the Model page reports no results at all** — it shows "version N, live" and nothing else, while
the training run measures CV average precision, test AP, AUC, the tuned cutoff, out-of-fold
precision/recall/F1 and every losing candidate's score. All of it is already stored in
`models.metrics`. Surfacing it is a read-only page, and it is what makes "did this retrain help"
answerable by the person paying for it.

**Model version history (decided 2026-07-31).** Not just the live model: every version, with its
metrics, as a history the user scrolls. `models` already keeps superseded rows — `is_active` flips
rather than deleting — so the data is there and the page is a read. What it buys is the only honest
answer to "is this getting better": v2 was Ridge with an untuned cutoff of 60 and v3 is svc at 38
with CV-AP 0.663, and a user should be able to see that happen rather than be told it did.

**Admin (added to the phase-4 admin surface, not built yet).** The same history, per user, for an
operator looking into an account — plus the setup-file completeness the training gate now computes.
The two questions an operator actually has are "why is this person's feed bad" and "did their setup
finish", and both are one page. Deliberately read-only: an operator reading a user's metrics is
support, an operator editing them is a different product with a different consent story.

### 6h — prove the setup chat, end to end ✅ **done 2026-08-03**

Walked on a fresh second account: signup → onboarding → the setup chat with its prompts and commands
→ training → a delivered job. **The owner's call was right, and the cost of not having done it
earlier was fourteen defects, none of which any test could have found.** The platform passed its
whole suite throughout and could not get a second person from signup to a delivered job.

Not one of them was an exception. Every failure was a component doing something reasonable with a
value nobody had written, or reading a different column from the one that had been. Three families,
recorded in full in `CHANGELOG.md` (2026-08-03):

1. **Two definitions of the same thing, disagreeing** — "profile ready", "is admin", "is a run in
   progress", "is this account due". In every case one copy was the one enforced and the other was
   the one the user could see, so the account looked finished and refused to work.
2. **Written here, read there** — the conversation writes profile *files*; four consumers still read
   columns only the superseded capture flow populated. The rubric was the expensive one: a complete
   13,809-character rubric, and a labeller answering `confirm_profile` forever.
3. **Nothing arms the first value** — delivery slots, and the rubric column before them. A field
   only ever *advanced* by the thing that consumes it, with nothing anywhere setting it the first
   time.

**The lesson worth keeping: the founder's account cannot prove anything.** Every one of these was
invisible on it, because a migration script filled its columns before the rebuild. A second fresh
account is the only instrument that finds this class, and it found a class that no amount of unit
testing was going to.

Three of the four sub-goals below shipped as designed; the third is what did the finding.

1. ✅ **`/setup` as the entry point.** Start submits `/setup` for the user, and the director runs the
   whole sequence from that one button.
2. ✅ **An overseer, not a free-running agent** — and the run found the reason it could never have
   held on prompt wording alone. **Only a message opening with a slash command named an agent**, so a
   chat started as the director ran the director exactly once and handled every reply afterwards with
   `/setup`, a prompt told (correctly for itself) to send the user to a new chat for anything it
   cannot do. That is why the handoff always landed at a step boundary. A conversation is now one
   command from end to end, which was the assumption the whole design rested on and the one thing not
   enforced.
3. ✅ **Watched on a fresh account.** The prompts were the last thing judged, and they were mostly
   not the problem: one real prompt defect (the environment block's handoff paragraph contradicting
   the director's body, last word winning) against thirteen defects in the plumbing around them.

### 6i — training is gated on a finished profile ✅ **done 2026-07-31**

`train_user` refuses unless every file the setup agent owns is present and filled. A model fitted on
a half-written profile is fitted against a rubric answering a different question, and nothing
downstream reveals it — the labels look like labels and the metrics look reasonable. Six existing
tests were training without a profile and now say so.

Also: `filled` is derived data in a column and had drifted from its content on a live account —
four files of real, marker-free text flagged empty. `grounding()` and `missing_setup()` read the
content and are immune; `listing()` reports the column, so the *agent* was told its own finished
files were still scaffolds. `repair_filled_flags()` recomputes from content; both accounts have been
repaired.

### 6j — the chat should look like a conversation ✅ **done 2026-08-02**

Owner's design note, deliberately kept small: each message in its own container, the user's on the
right and the model's on the left, with a rail of dots down the side showing what the agent is
doing — the shape Claude Code uses for tool calls. **Identical whether the account is on OAuth or an
API key**: the transport is not a thing a user should be able to infer from the layout. Not a
redesign, just the containers, the sides, and the activity rail.

### 6k — the chat surface, after the setup flow works ✅ **done 2026-08-03**

Deliberately queued *behind* 6h, and it stayed there: polishing a flow that cannot complete is the
wrong order, so this shipped only once a fresh account had been through one.

1. ✅ **Per-step containers with a dot rail** — one container per step of the reply, dots down a
   vertical line, the shape a coding agent uses.
2. ✅ **A processing indicator**, and the send button *becomes* the stop while a turn runs. Two
   controls with one of them dead, at the moment there is exactly one thing a person wants to do,
   was the version that shipped first and was wrong.
3. ✅ **Stop** — a state flip the worker checks between tool steps, not a killed request. The
   training Stop needed the same treatment and did not get it until 2026-08-03: cancelling queued
   rows could never end a loop that re-enqueues itself, so it kept spending the user's credential
   while the page said it had stopped.
4. ✅ **Model version history**, on the Model page and per user in the admin panel — every version
   with its head, CV-AP, test-AP, tuned cutoff and out-of-fold precision/recall, which used to
   require SQL by hand.

### 6l — one fetch per job, one cadence for everyone ✅ **done 2026-08-03**

**The premise: this app streams the newest postings.** Everything below follows from taking that
literally rather than making it configurable.

**1. LinkedIn fetches the whole posting on first sight** ✅. The link pass used to store a *snippet*,
the row was embedded and scored on that, and an hourly `linkedin-describe` replaced it with the real
JD — invalidating the vector and triggering a rescore. That self-heal was not a safety net: it was a
wrong score that existed for up to an hour, and a job that may have been silently rejected on a
preview nobody re-examined.

**links → dedup → fetch the full JD only for the rows that are new → store complete → embed →
score**, all on the same lease in the same pass (`services/harvester/linkedin.py`). The objection to
inline fetching was that one posting would be fetched once per interested user; deduping *before*
the fetch removes it, because a job already in the corpus is never fetched again by anyone. One
proxy hit per new job — not per user, and not per user per hour. Measured on the live box: **4,989
of 5,111 LinkedIn rows from the last seven days carry a description.**

Asking jobspy for descriptions directly (`linkedin_fetch_description: True`) is **not** the way to
do it — it would fetch every result including the ones already held, which is the cost the dedup
exists to avoid.

`linkedin-describe.timer` and its service are deleted, and **so is the entry that kept asking for
them**: the timer was disabled on the box while `check_health.py` and `install-units.sh` both still
listed it as required, so every health run told an operator to re-enable the pass this item exists
to remove. A required-timer list naming a timer the design deliberately deleted is worse than not
checking — it trains the reader to ignore the one alarm there that has caught a real outage.
`--describe-linkedin` survives as a manual backfill for the rows a pass could not reach (122 of
them today); what is gone is the schedule.

**2. Freshness becomes admin-set, per source** ✅ **done 2026-08-04** — and it was ticked a day early
on 2026-08-03, which was wrong and is worth recording. The setting existed, the admin panel rendered
it, and **`freshness_hours` was called by exactly one caller: the admin route that renders it.** The
harvest computed its own window from user configs, so an operator moving this number moved a number
on a page. Ticking "done" off the existence of a setting rather than off a consumer reading it is
the same mistake as the label budget and the rubric column, made while documenting those two. Wired
and measured in 6s.

**3. One delivery cadence: 15 minutes, for everyone** ✅ **done 2026-08-03.** `delivery_interval_min`
is operator-owned and `users.notify_interval_min` is no longer read anywhere. The only things a user
tunes now are their keywords and locations.

**4. Latency is a requirement, not a side effect** ✅ **done 2026-08-03, and it needed a new column.**
Measured per zone rather than end to end, which is 6p — the end-to-end number cannot say which of six
handoffs ate the time. The middle of the pipeline turned out to have no clock at all: `corpus` went
from `description_fetched_at` straight to having a vector, so two of the six handoffs were
unmeasurable and both hid inside one found → scored number. `corpus.embedded_at` fixes that, and is
deliberately not backfilled.

**Still open, and now quantified:** what happens to a row whose JD fetch fails permanently — never
scored, or scored on what little there is after N attempts. **122 rows are sitting in that state**
(2.4% of seven days of LinkedIn), currently invisible to every user because an unembedded row is
never a candidate. Small enough not to be urgent, large enough that "it self-heals" is not the
answer. Not yet decided.

### 6m — a monitor that survives the thing it monitors (added 2026-08-01)

**Owner's observation, and it is the right one: a status page hosted on the box it reports about
tells you nothing at the only moment you need it.** If the VPS is down, Netdata is down, the admin
panel is down, and the site is down — so the reader sees a connection error and cannot tell a dead
host from a dead network from a dead browser tab.

What that implies, in order of cost:

1. **An external check that is not on the VPS.** A third-party pinger (Better Stack, Healthchecks,
   UptimeRobot) hitting `/health` from outside, alerting by email or Telegram. Cheapest possible
   fix and it covers the actual question — is the box reachable.
2. **A dead-man's switch**, which catches the failure a pinger cannot: the platform *appearing* up
   while the pipeline has stopped. The dispatcher checks in on every pass; a missed check-in is an
   alert. The inverse of a ping, and it catches a stuck worker on a healthy host.
3. **Metrics that outlive the host.** Pushing latency and queue depth somewhere off-box so the last
   known state is readable when the box is not. Only worth doing once 1 and 2 exist — an operator
   who knows the machine is down does not usually need its metrics from ten minutes ago.

Deliberately not built yet. Netdata is deferred with it: per-second host metrics are useful and
neither of them answers "is it up" from outside.

### 6b — close the feedback loop

`record()` writes a `feedback` label at weight 5 and the webhook calls it, and production has
**zero of them** against 3,499 `llm` labels. Two decisions:

1. ✅ **Done.** **The 98 imported applications — decided 2026-07-31: do it.** Real applications the founder made,
   sitting as `applied_at` timestamps that never became training data. 98 true positives at 5×
   weight against a pool of 3,498 LLM guesses is the single largest quality win available. The
   argument against was that they predate the current profile generation; the owner's answer is that
   an application is the strongest signal the platform will ever get about what he actually wants,
   and a rubric edit does not make it untrue. **A person applying to a job is ground truth; an LLM
   scoring it is an estimate.** Backfill writes `source="feedback"` at `FEEDBACK_WEIGHT`, stamped at
   the current generation, and the next retrain consumes them.
2. **Prove one live tap**, end to end: tap Build CV in Telegram → a `feedback` label appears →
   a retrain consumes it. Until that has happened once, the flywheel is untested in production.

   **Still open, and now measurable.** Production holds exactly **98 `feedback` labels against 2,788
   `llm`** — 98 is the backfill count, so not one has come from a tap. The second account started
   receiving jobs on 2026-08-03, which is the first time anyone but the founder has had a card to
   tap, so this is finally testable rather than blocked.

### 6c — the two sweeps that never run

`mark_dormant` and `prune_model_artifacts` are implemented, tested, and called by nothing. Dormant
accounts keep consuming harvest budget and their own LinkedIn tier; every model artifact ever
trained is still on disk. Both want a caller on a schedule — a small unit, or a step in an existing
timer.

### 6d — the cheap operational debts

- **Security headers and `allowed_hosts`** (`AUTH_REUSE.md` §5). No CSP, HSTS, `X-Frame-Options` or
  `TrustedHostMiddleware`. The tunnel being the only way in is why nothing has broken, which is not
  the same as having decided.
- **Runbooks** (`OPERATIONS_PLAN.md` §9), still unwritten.
- **Five dead-lettered jobs** on the box — 3 `train`, 1 `label`, 1 `process` — nobody has read.
- **Cover letters have no trigger.** The prompt is ready; nothing says when a posting wants one.

### 6e — the unclaimed 2x

Batch API (−50%) and prompt caching are both available per user under BYOK and neither is used;
cold-start labelling is exactly the offline workload the batch path is for. Measure the rubric
against Haiku's 4096-token cache floor before assuming caching buys anything.

### 6g — duplicate postings reach Telegram (added 2026-07-31)

Of 14 cards delivered in the first hour after the retrain, **8 were repeats** — the same Golang role
six times at six scores, one per Polish city. Corpus-wide: **589 redundant rows in 7 days** across
296 groups, one at 22 copies.

**Dedup is not broken.** It runs before insert and narrows url → canonical_url → apply_url →
content_key. The copies survive because `content_key` is `company|title|location` *by design*, and a
posting listed in 22 cities is 22 distinct keys.

The differing scores are not a country preference: `job_features` takes only title and description,
and the model has no location input. Each city's page carries its own wording, so the embedding
reads different text — all six descriptions hit the 4,000-character ceiling and five of six differ.
The 39–45 spread is noise on one job.

**Decision taken:** keep the rows, collapse at delivery. Multi-location postings are legitimately
distinct URLs a user may want, and rewriting the ingest key would discard genuinely separate city
listings. One card per `(company, title)` group, highest score, is the smaller and more reversible
change — and it is the user-facing problem.

**Exit criteria for Phase 6:** the corpus is on one embedding version, both models are trained on
it, a tap in Telegram has produced a label that a retrain consumed, and no user receives the same
role twice in one digest.

**Measured 2026-08-03 — two of four.**

| Criterion | State |
|---|---|
| Corpus on one embedding version | ✅ `select count(distinct embedding_version)` = **1** |
| Both models trained on it | ✅ owner v4, second account v1 |
| A tap produced a label a retrain consumed | ❌ 98 `feedback` labels, all from the backfill (6b.2) |
| No user gets the same role twice in a digest | ❌ not built — 6g's collapse-at-delivery is a decision, not code |

The two outstanding are both in 6g/6b and both now *testable* rather than blocked: until 2026-08-03
only one account received cards at all, so neither duplicate delivery nor a live tap could be
observed on anybody but the founder.

### 6n — the corpus an operator can see and seed (added 2026-08-03)

**Why.** A new user's training data is whatever the shared corpus already holds for their field, and
the corpus only holds what previous users searched for. The second account on the platform found 240
matching postings against 14,359 rows, labelled ten of them, and stalled — not through any bug, but
because the corpus had accumulated for weeks against one software search and hours against a
mechanical engineering one. Every user after the first inherits that shape.

Four pieces, in this order. The first two are additive and safe; the fourth can silently cost people
jobs and must not be built before it can be measured.

**1. A corpus browser in the admin panel** ✅ **done 2026-08-03.** Search across every posting,
without scores — scores are per user and this is the shared table. Source and date facets, so "what
is actually in here" is a question an operator can answer without SQL.

**2. Operator keywords** ✅ **done 2026-08-03.** Terms an operator injects into the query set to fill
the corpus ahead of the users who will need it. **Stored separately from user keywords and visibly
distinguished**: an operator may delete their own and must not be able to delete somebody's real
search term, which is theirs and is the thing their feed depends on — so there is no control for it
and no route behind it. Both lists are searchable and paged; twenty-odd terms in an unordered grid
read as a wall.

**3. Latency thresholds in the admin panel** ✅ **done 2026-08-03**, and then extended into a
per-zone drill-down (6p) because a judged end-to-end number still cannot say *where*. Green/amber/red
on the **p90**, not the median — half the jobs arriving promptly is not the claim this product makes.
Thresholds come from the cadences the pipeline actually runs on, and a test pins them to
`DELIVERY_INTERVAL_MIN` rather than to literals, so changing the cadence fails the build instead of
quietly making amber meaningless. Below 20 samples the verdict is "not enough data" rather than a
colour: a percentile over three rows is arithmetic, not evidence.

**4. A prefilter before scoring — and not on keywords.** Every user currently scores every embedded
corpus row: `score_lane` filters on `embedding IS NOT NULL` and the model version, and nothing else.
Seed the corpus to 100k and each account does 100k scorings. **The obvious fix is one this project
already measured as harmful**: only 239 of 376 jobs scoring ≥60 had a keyword in the title, so a
keyword prefilter would have discarded a third of the good matches — the unusually worded ones, which
are often the interesting ones. The lever is the embedding already on every row: cosine similarity
against the user's profile vector, cheap in pgvector, and it keeps the job titled "Vehicle Dynamics
Specialist" that no keyword list would catch. **Ship with a before/after count of how many ≥60 jobs
the filter would have dropped**, or it is not shippable.

**Also worth doing, unmeasured.** The query set is `keyword × location` and is naive about
redundancy: `european_union`, `europe` and `budapest` are three stems returning largely the same
postings, and a source like NoFluffJobs filters by city only, so a region-level stem is meaningless
there. Collapsing subsumed locations per source would buy budget back. Nobody has measured how much.

### 6o — the harvest's own inefficiencies, measured before touched (added 2026-08-03)

Three known-suboptimal things in how demand becomes queries. **None has been measured**, which is
why they are one entry rather than three tasks: the first job in each case is to find out how much
it actually costs, because the obvious fix to the first one was already measured as harmful.

**1. Scoring has no prefilter.** `score_lane` selects every embedded corpus row the user's current
model has not seen -- `embedding IS NOT NULL` and a version comparison, nothing else. A 100k corpus
means 100k scorings per account. Free in money, not free in VPS. The keyword version of this filter
would have dropped a third of the jobs scoring ≥60 (239 of 376 had a keyword in the title), so the
lever is the embedding already on every row. **Not shippable without a before/after count of good
matches lost** -- this is the change that can quietly cost somebody a job.

**2. `select_within_budget` is fair but blunt.** Round-robin across users with carry-over, which is
right, and it says nothing about whether a query is worth running. A stem that returned nothing but
duplicates for ten cycles still gets its turn ahead of a fresh one. Something like a per-stem yield
history would spend the same 60 queries better. Unmeasured: nobody knows the current hit rate per
stem, and that number has to exist before the policy changes.

**Now the live constraint, since item 3 connected the budget to real requests.** 318 queries at 60
per cycle is **six cycles, ~90 minutes, to serve every query once** — against a product whose whole
claim is a 15-minute stream. Two things to settle, in this order:

- **Is 60 still the right number?** It was sized for JobSpy ban risk and bandwidth on a proxied
  scraper. The eight sources it now governs are cheap unproxied REST calls, and the cycle spends
  most of its time in embed (78 s) and describe (39 s) rather than in gather (22 s). The budget may
  simply be too tight for the tier it ended up governing.
- **Nothing records which query found which row.** That is the instrumentation the yield history
  needs and it does not exist; `corpus` has a `source` and no query. Until it does, "this stem
  returns nothing but duplicates" is not a claim anybody can check.

**3. Stems are redundant across locations** ✅ **measured and largely fixed 2026-08-03.**

Measuring it first found something bigger than the redundancy: **the query set was wired to nothing
at all.** `cycle.py` built the fold for `("indeed", "google")` — neither enabled, and not even the
registry's keys for them (`jobspy_indeed`, `jobspy_google`) — so every planned query carried a source
name `_gather` could never match. The whole apparatus, the fold, the round-robin, the carry-over,
governed **zero requests**. Meanwhile `union_keywords` and `union_locations` read the top level of a
config, where nothing has ever put them, so the nine boards that *do* read `cfg["queries"]` each got
one empty search and fetched their unfiltered recent feed. And the query they got was
`{"search": term}` — a shape only two of the nine read.

Three defects, one symptom: a corpus filled by whichever board answers broadest.

With that fixed, the redundancy is real and now quantified against the live configs:

| | queries |
|---|---|
| Before — built for two sources that are not enabled | 234 (issuing nothing) |
| Naively pointed at the eight real sources, every axis crossed | 936 |
| **With each source's unusable axes collapsed** | **318** |

**66% of the naive query set was byte-identical requests.** Most boards take a search term and
nothing else, so crossing their keywords with six location slugs produces six identical HTTP calls,
six times the budget, and one set of results. Each source now declares what it can filter on
(`QUERY_SHAPES`), and the fold collapses the axes it cannot use. Per source: HiringCafe 117 (the one
free-tier board with a real location model), Arbeitnow 33 (no geography, but a remote flag it does
honour), everyone else 28 — one per keyword.

Collapsing an axis is **not** a narrowing: the user still asked for those locations, and the source
was never going to apply them.

**What remains is exactly what the entry above predicts, now isolated to one source.** HiringCafe
carries 117 of the 318 because `europe`, `european_union` and `budapest` are nested and it accepts
all three. Subsumption is still deliberately unimplemented (result caps make a broader query a
*truncated* superset), so this wants a per-source subsumption table rather than a general rule.

### 6p — the latency drill-down: one row per zone ✅ **done 2026-08-03**

**The end-to-end number is the one worth publishing and the one least useful for fixing anything.**
"A posting took 36 minutes" does not say which of six handoffs ate them, and each of those is
repaired somewhere different — a stuck describe worker, an embedder behind a flood, a score lane
with no free worker, and a delivery cadence are four unrelated problems wearing the same number.
6n item 3 gave the number a verdict; this gives it an address.

A posting arrives as a **link** and leaves as a **card in Telegram**. Between those it crosses five
boundaries, and each one is a place work is picked up, done, and handed on. Those boundaries are
the rows:

| Row | From → to | Whose work |
|---|---|---|
| Posted → found | the site published it → the harvest saw the link | harvester, 15-min cadence |
| Found → described | link → the JD text is stored | describe pass (+ proxy for LinkedIn) |
| Described → embedded | text → a vector | embedder gateway |
| Embedded → scored | vector → this user's score | `score` lane, per account |
| Scored → delivered | score → the card sends | `notify` lane, 15-min cadence |
| **Found → delivered** | the whole thing | what the user actually waited |

**Columns are the stats; the row expands to the diagnosis.** Collapsed, a row is median / p90 / p99
/ worst / volume / verdict — enough to see *which* zone is the problem and nothing more. Expanded,
it answers *why that zone*, and the four things that answer it are different in kind from
percentiles, which is exactly why they belong behind a disclosure rather than in twelve more
columns nobody reads:

1. **Waiting versus running**, from the queue's own timestamps, wherever a lane does the work.
   `created → claimed` is waiting and means too few workers; `claimed → finished` is running and
   means the work itself got slower. One "how long did it take" hides which, and they are fixed in
   completely different places.
2. **The backlog right now** — how many rows are sitting at this zone's door, and how old the
   oldest one is. A percentile describes what already got through; a queue of 4,000 describes what
   has not, and only the second one predicts tomorrow.
3. **An hourly series for that stage.** A snapshot cannot show a spike, and the spike is the thing
   worth chasing: a p90 of 36 minutes over a day hides the hour it was four times that.
4. **Why the threshold is where it is**, in words, next to the number it judges. A colour whose
   reasoning lives in a Python constant is a colour an operator learns to ignore.

**Two honesty constraints, both of which cost something.**

*Embed had no timestamp at all.* `corpus` records `description_fetched_at` and then jumps straight
to `embedding_version` — there was no way to ask when a vector was written, so described → embedded
and embedded → scored were unmeasurable and the gap was hidden inside "found → scored". Adding
`corpus.embedded_at` is the change that makes the middle of the pipeline visible. Historical rows
are NULL and stay NULL: **backfilling it from `first_seen_at` would invent a measurement**, so
those two rows read "not measured yet" until the column fills, and the expanded row says so rather
than showing a zero.

*Posted → found is reported and not judged.* Sources round `posted_at` — LinkedIn says "2 days
ago" — so the number is real but its precision is a source's, not ours. It gets no threshold and no
colour, because a red pill produced by rounding is the exact failure 6n warned about. It is on the
page because harvest lag is genuinely part of what a user waits, and leaving it out would make the
other five rows look like the whole story.

**Done when** an operator can open Latency, see which of six zones is amber, expand it, and come
away knowing whether to add a worker, chase a source, or leave it alone — without an SSH session.
✅ Shipped. Two rows read "not measured yet" until the corpus turns over on the new column, which is
the honest state rather than a backfilled number.

### 6q — document building still reads the profile the capture flow wrote (added 2026-08-03)

**Blocking, and it sits directly between a trained account and a finished CV.**

Two sites in `services/docgen/run.py`: **line 92** calls
`ProfileSchema.model_validate(user.profile)`, and **line 245** is the same "no confirmed profile"
gate the trainer had, which refuses before the build even starts. Fixing one without the other moves
the failure rather than removing it. That JSON was
written by the document-capture step the setup conversation replaced, and nothing sets it any more,
so an account built the current way has `user.profile = None` and this raises. The first CV that
account tries to build will dead-letter with a pydantic error in a queue row, which is exactly how
the previous five instances of this presented: no error on any page, and a lane quietly failing.

**Not fixable the way the scorer was.** Scoring could fall back to an empty feature spec because the
embedding carries the signal and the keyword terms are an addition. A document cannot: it needs the
person's name, their experience, their skills. Falling back to an empty schema would produce a CV
about nobody, which is worse than a refusal.

So docgen has to read the **profile files** — `01-candidate-profile.md`, `context.md`,
`05-cv-templates.md`, `base_cv.html` — which is where that information now lives, and which is what
`tailor_cv.md` and `tailor_cover.md` were written to consume. The prompts already assume those
files; it is the code around them that still assumes the schema.

Worth checking whether `ProfileSchema` is needed at all downstream or only as an accessor, because
the answer decides whether this is a rewrite or an adapter.

**This is the sixth and last known instance of one assumption**: setup writes one place, a consumer
reads another. The others were the Model page gate, the process action, `users.rubric_md`, the
trainer's gate, and the scorer's feature spec. Each was found by something failing in production
rather than by a test, which is the argument for grepping the remaining callers of `user.profile`
before the next user signs up rather than after. The survey as of 2026-08-03: `process_routes.py`
lines 82/86/101 read it and are already correct — they treat a finished set of files as a profile —
and `trainer/train.py` 77/109 are fixed. `docgen/run.py` 92 and 245 are what remain.

### 6r — the budget belongs to a source, not to the platform (added 2026-08-04)

**The owner's question, and it dissolves the problem: why is there a global limit at all?**

`HARVEST_MAX_QUERIES_PER_CYCLE = 60` comes from `HARVESTER_PLAN.md` §3, and it was sized for the
JobSpy tier — proxied, ban-prone, bandwidth-billed, one residential IP. After 6o.3 connected the
query set to the sources that actually read one, that number governs **eight free unproxied REST
boards**, where none of its reasoning applies. 318 queries at 60 a cycle is six cycles, ninety
minutes, for one full round, against a product whose entire claim is a fifteen-minute stream.

**Users are not the constraint, and it is worth being precise about why.** The corpus is shared, so
a query is `(source, keyword, location)` — it is never *for* anybody. The user set on a
`PlannedQuery` exists for attribution and quota accounting only. So the query set grows with
*distinct* keyword-location pairs and saturates as users overlap; the tenth user asking for "python
developer" costs nothing. What scales badly with user count is only the round-robin **ordering**,
not the work.

Sources already run concurrently (`ThreadPoolExecutor`, eight wide), and queries within one source
run serially, which is right — serial is what respects a rate limit. So the real wall clock is the
slowest single source, not the sum. Twenty-eight keywords at one request a second is thirty
seconds, in parallel with everything else. It fits in a cycle with room to spare, and the ninety
minutes is an artefact of the cap rather than a fact about the work.

**The actual constraints, measured:**

| Constraint | Reality |
|---|---|
| Result cap per query | ~100 rows (Remotive, Himalayas, NoFluffJobs, Consider), 120 HiringCafe, 40 Getro. **Per query, so more queries reach deeper** — this is also why subsumption is unsafe: a broader query returns a *truncated* superset |
| Rate limit per source | Real and per source. Arbeitnow answered 13 of 33 burst queries with non-JSON; RemoteOK needs 2 s between requests |
| Wall clock | ~22 s gather against a 15-minute cycle, with embed (78 s) and describe (39 s) dominating |
| Bandwidth | Only the proxied LinkedIn tier, which is metered per user and already governed separately |
| Corpus size | Not a constraint. 15k rows against a 400k projection |

**The redesign, in order:**

1. **Per-source budgets, replacing the global one.** Each source gets a ceiling matched to its own
   rate limit, with the global number kept only as a safety ceiling far above the sum. Remotive
   being starved has nothing to do with HiringCafe being expensive, and a single cap makes them
   compete for a resource they do not share.
2. **Round-robin over distinct keywords within a source, not over users.** The fold has already
   deduplicated across users by this point, so keyword-level fairness *is* user fairness the moment
   demand overlaps — and adding a user with terms somebody already asked for then costs nothing at
   all. This is the change that removes user count from the constraint.
3. **Two live defects in the current implementation**, both visible in a six-cycle simulation of the
   real config:
   - Sources are drained one at a time. `_stable` sorts by `QueryKey`, whose first field is
     `source`, so each user's queue is grouped by source and one board is finished before the next
     is touched. **Remotive is not queried until cycle 5** — seventy-five minutes — and every source
     is silent for an hour at a stretch.
   - `if key in taken: continue` costs a user their whole turn when a query is shared, rather than
     advancing them to their next unserved one. Latent at two accounts with zero overlap; it bites
     exactly as overlap grows, and it systematically serves users with popular terms last.
4. **HiringCafe is then the only long pole** — 117 of the 318, because it is the one free-tier board
   with a real location model and `europe`, `european_union` and `budapest` are nested. That is a
   per-source subsumption table, not a general rule, and it stays behind the result-cap argument.

**Not part of this:** a per-stem yield history (6o.2) still cannot be built, because nothing records
which query found which row. `corpus` has a `source` and no query.

### 6s — every source needs a keyword and a time filter (added 2026-08-04)

**The owner's rule, and it is the right split: keyword and time are primary, location is not.**
Location, remoteness and contract type can all be recovered from a posting we already hold, so they
are per-user filters applied at scoring and delivery. Freshness and subject cannot be recovered —
a job we never fetched is a job nobody can score.

Audited 2026-08-04, and the picture is uneven:

| | keyword | location | time |
|---|---|---|---|
| Remotive, Himalayas, NoFluffJobs ×2, Consider, Getro | server-side term | none | client-side |
| Arbeitnow | server-side term | none (remote flag only) | client-side |
| HiringCafe | server-side term | real region model | **server-side** |
| Jooble | server-side | server-side | client-side |
| ATS family ×9, RemoteOK, WeWorkRemotely, WorkingNomads, Jobicy, TheMuse, Profession, DreamJobs, JustJoinIt | **none** | none | client-side |

**Time is now real** ✅ **2026-08-04.** `freshness_hours` was rendered in the admin panel and read
by exactly one caller — the admin route that renders it — while the harvest computed its own window
from user configs. It is passed into `build_source_configs` now, per source, and reaches
`posted_within_hours` as well as `max_age_hours`, because the ATS family's `JobFilter` prefers the
former and a flat platform constant of 72 was outranking everything.

The defaults are set from three days of measured ingestion rather than taste. A uniform six-hour
window would have kept 99.9% of Arbeitnow, 41% of Greenhouse, **4% of Recruitee and 2% of Workday**:
a company board is reached when our harvested token list gets to it, which has nothing to do with
when the job was posted. One number is not a simplification here, it is a 96% loss on two sources.
And a stored override map is now merged over those defaults rather than replacing them — the live
setting names three sources, so replacement silently dropped every other source to six hours.

**Keyword is the open half.** Eight sources have no search parameter upstream at all: RemoteOK
serves a full dump, WeWorkRemotely is an RSS feed per category, WorkingNomads returns everything.
For those the only possible keyword filter is client-side, and **that is the one this project has
measured as harmful** — 239 of 376 jobs scoring ≥60 had a keyword in the title, and turning it on
took eleven sources to zero ingestion for six hours on 2026-08-03. So the answer for a feed-shaped
source is to fetch it, apply the time window, keep everything, and let per-user scoring decide,
which is what "harvest broadly, match per user" has always said. Worth checking per source whether
an unused upstream parameter exists — Jobicy takes a `tag`, TheMuse takes categories — since a
server-side filter is a different mechanism from discarding what already arrived.

## Phase 7 — the pipeline, in the order it has to be right (added 2026-08-04)

**The owner's sequencing, and it is the correction to how this work was being picked up.** The
entries below were being treated as a menu; they are a chain, and doing a later one first means
building on something unverified.

> **Stage 1 — the queries are right.** Every source is asked in the shape it reads, with the
> keywords a user actually typed and the time window an operator set, and each one returns results
> we can see. Nothing downstream is worth building while a source is being asked a question it
> ignores. → **6t**, and the verification below.
>
> **Stage 2 — the corpus stores a record, not a blob.** Jobs arrive as structured rows: title,
> description, country, field, and the rest. Extraction happens once at ingest, for everyone. →
> **6w**, **6z**.
>
> **Stage 3 — filtering is fast and happens before scoring.** Only the jobs a user could plausibly
> want reach their model. → **6u**, **6x**.

Stage 1 is not "the code is written". It is: for **every enabled source**, we have seen that the
keyword reaches it, the window reaches it, and rows come back. That check does not exist yet and is
the immediate next piece of work.

### 6t — sources, and the seven taken out (decided 2026-08-04)

**Every source was pinged from the production box** with a real keyword, an unrelated keyword and
nonsense, and asked whether the answer changed. Seven answered identically to all three. A source
that cannot be asked for anything is not part of a targeted harvest — it is a feed we re-ingest and
filter afterwards, which is a different design and a post-beta decision.

| Source | Why it went | Cost |
|---|---|---|
| **arbeitnow** | identical 175 results for `engineer` / `nurse` / `zzqqxwv` / empty | 4,236 rows in 7 days, the largest single contributor — and all 33 of our queries a cycle refetched one page |
| **remotive** | identical 31 results for the same four | 2 rows |
| **remoteok** | `/api` is a full dump, no search parameter exists | 233 rows |
| **dreamjobs** | 1 result, unchanged by any term | 0 rows |
| **consider** | iterates `cfg["boards"]`, which nothing sets | **0 rows, ever** |
| **getro** | the same | **0 rows, ever** |
| **nofluffjobs_hu** | identical config to `nofluffjobs`, so the identical request | 0 rows — every one deduplicated away |

Roughly 35% of intake, almost all of it Arbeitnow. **Accepted deliberately**: the owner's call is
that sources and queries have to be correct before volume is worth anything, and a feed we cannot
target is volume rather than coverage.

**What remains, and how each is asked:**

| | keyword | measured |
|---|---|---|
| himalayas, nofluffjobs, hiringcafe | server-side, via `cfg["queries"]` | 6/0, 123/0, 40/0 |
| profession | server-side, one request per keyword | 15/0 |
| justjoinit, jobicy, themuse, workingnomads, weworkremotely | client-side over **title + description + tags** | 4→1, 17→13, 24→21, 28→5, 18→9, all 0 on nonsense |
| ATS ×9 | none — a company board has no search API | filtered only by time |
| linkedin | JobSpy, a real keyword search | per-user, own proxy |

**Post-beta, in this order.** 1. Arbeitnow and RemoteOK back as *pure feeds* — one unqueried fetch
per cycle each, targeted by the corpus filter in 6u rather than by a query. That is 4,469 rows for
two requests. 2. Consider and Getro need board lists before they can return anything. 3. JobSpy
already reaches **Indeed and Google** through the same adapter as LinkedIn, which is the cheapest
real coverage increase available and needs only enabling and measuring.

**Two rules now settled for the query set:** keyword and time are the only axes. Time comes from the
operator's per-source setting in the admin panel and nothing else. Location is gone from queries
entirely — one board has a location model worth the name, every location a user picks multiplies
that board's query count, and a posting's country is on the posting.

### 6u — the corpus knows what a job is, so the scorer stops reading all of it (added 2026-08-04)

**Owner's design, and it replaces the prefilter argument in 6n.4 / 6o.1 with something safe.**

Today `score_lane` selects **every embedded corpus row** the user's current model has not seen —
`embedding IS NOT NULL` and a version comparison, nothing else. Every account scores the whole
corpus. At 15k rows that is merely wasteful; at the 400k projection it is the platform's largest
recurring cost, and it is paid per user.

The rejected fix was a keyword prefilter, and it was rejected on measurement: 239 of 376 jobs
scoring ≥60 had a keyword in the title, so it discards a third of the good matches — the unusually
worded ones, which are often the interesting ones. **The owner's version avoids that entirely,
because it filters on facts rather than on wording.**

**At ingest, strip and store what a job factually is.** Country, region and city; salary and its
currency and period; contract type; seniority where the posting states it. These are already in the
text and are thrown away today — `corpus` keeps a free-text `location` string and nothing
structured. Stored as columns beside the link, title and description, they make the cheap questions
cheap:

- **Country.** The largest and safest cut. A user searching Hungary and the EU has no use for a
  Texas posting, and no amount of good wording changes that — unlike a job title, a country is not
  a matter of phrasing.
- **Job field**, from the user's own profile rather than their keyword list, so it stays the
  embedding-based judgement 6o.1 argued for rather than a term match.

Then scoring reads a filtered set: **only the rows a user could plausibly want**, which is also what
makes the per-user cost stop scaling with the whole corpus.

**Same shipping condition as 6o.1, and it is not negotiable:** ship with a before/after count of how
many ≥60 jobs the filter would have dropped. A country filter is much easier to defend than a
keyword one — a posting's country is a fact, and the failure mode is a mis-parsed location rather
than an unusual phrasing — but "easier to defend" is not "measured".

**This also gives the location axis a home.** It came out of the queries in 6t precisely because
this is where it belongs: asked once against the corpus, per user, on a parsed field — rather than
multiplied into every board's query set to be applied by boards that mostly cannot apply it.

**Country is a hard filter, and the trick is what "EU" means** (decided 2026-08-04).

- **Hard, not a weighting.** A user who did not choose the UK must not receive UK jobs, and no
  amount of a good title should override that. Today nothing enforces it anywhere: `_location_points`
  awards *full marks* to any posting when the user picked a region, with the comment "no posting
  spells `european_union` in its location field… the region is treated as satisfied". So selecting
  the EU currently excludes nothing at all, and a London job scores exactly like a Berlin one.
- **An unknown country passes.** Plenty of postings name no place, and a remote role frequently
  names none on purpose. Rejecting those would throw away the most portable jobs on the board, so
  absence of evidence stays what it already is elsewhere in this codebase: not evidence of absence.
  That is also what keeps the filter cheap — one indexed comparison and a NULL check.
- **The EU is a list, and the UK is not on it.** `european_union` expands to the 27 member states;
  `europe` expands to those plus the UK, Switzerland, Norway and the rest of the continent. A user
  selecting Germany gets Germany. A city implies its country — `budapest` admits Hungary — because
  somebody who named a city has certainly not excluded the country around it.
- **`remote_worldwide` disables the filter** rather than expanding to every country, since the point
  of that selection is that place does not matter.

**Fully tested is a requirement of this entry, not a nicety.** The failure is invisible: a filter
that is too tight silently costs somebody a job they would have taken, and nothing in the product
reports a job that was never scored. The tests have to cover the expansion of every location slug,
the UK-is-not-EU case in both directions, the unknown-country pass, and a job whose location string
is a city with no country in it.

Salary and contract type are deliberately **not** in this first cut. Country and job field are what
make the scorer's cost stop scaling; the rest is a later addition once this one is proven.

### 6w — a corpus row is a record, not a blob (added 2026-08-04)

**The owner's design, and it reframes three separate problems as one.** A corpus row today is a
link, a title, a free-text location and a wall of description. Everything anybody wants to know
about a job — where it is, what field it is in, what it pays, whether it is a contract — is in that
wall, and the only tool for reading it is a language model at scoring time. So the wall is read
once per user per job, which is the cost that scales worst on this platform.

**Extract at ingest, once, for everyone.** Country (6u) is the first field; `job_field` is the
second and the more valuable. Then a row carries what it *is* beside what it says, and three things
fall out of the same change:

1. **Prefiltering costs a millisecond.** `country IN (...) AND job_field = ...` is an indexed
   comparison. The alternative that was rejected — matching keywords against a title — cost a third
   of the good matches (6o.1). A field and a country are facts; wording is not.
2. **Training gets cleaner inputs.** The embedding is built from title + description
   (`libs.corpus.embedtext`). Once the country lives in its own column it can be *stripped* from the
   text rather than left in it, so the model stops seeing geography at all — which is what
   `embedtext` already says it wants and cannot currently guarantee.
3. **Reading the corpus becomes possible for a human.** The admin Corpus tab shows title, company,
   a location string and a `usable` flag, because that is all a row holds.

**`job_field` is its own piece of work — see 6z.** It belongs to Stage 2 with the rest of the
extraction, and it is large enough and interesting enough to have its own entry rather than a
paragraph here.

**The rule that makes a mistake survivable, and it governs every field in this entry: no label means
no filtering.** A wrong country is recoverable, because the posting stays in the corpus and a user
who widens their selection sees it. A wrong `job_field` hides a job from the only user who wanted
it, permanently, with no symptom. So a row whose field is unknown or low-confidence is **passed
through to scoring**, exactly as an unreadable country is. The filter only ever removes rows it is
confident about, and every version ships with the before/after count of ≥60 matches lost.

**Also open: recycling.** The owner's concern is that a job filtered out for a user keeps being
re-examined every cycle for ever. **The answer is that there is nothing to remember**, and it is
worth writing down why. `score_lane` selects rows where `scored_model_version` is null or stale; a
filter is a `WHERE` clause in that same statement, so a row that fails it is never scored and never
written to `user_job_state`. Re-testing it next cycle is an indexed comparison, not a model call —
microseconds against the thing that actually costs, which is scoring. Recording rejections would
mean a row per user per rejected job (400k × every account), to save a cost that does not exist, and
would then need invalidating every time somebody edited their countries. **Not remembering is
strictly better**: a user who widens their search sees the previously-excluded jobs immediately,
with no backfill and no stale exclusion to clear.

### 6x — the filters a job can be judged on (added 2026-08-04)

Extracted at ingest as 6w describes, then available as cheap pre-score filters. In priority order:

1. **Country** — 6u, in progress.
2. **Working arrangement** — remote / hybrid / on-site. **Already separate and staying that way.**
   The location vocabulary used to carry `remote_eu`, `remote_uk`, `remote_us` and
   `remote_worldwide`, which made "where" and "how" one question whose answers could contradict each
   other, and duplicated the `remote_policy` field the config already had. Removed 2026-08-04;
   `libs.corpus.workfilter` owns this.
3. **Job field** — the classifier in 6w.
4. **Contract type** — full-time, part-time, contract, internship, temporary. Stated in most
   postings and currently discarded. A user wanting permanent work has no way to say so.
5. **Salary** — already parsed by `libs.corpus.salary` for the feed card and not stored as a
   filterable field. Deferred by the owner until country is proven.
6. **Seniority** — junior / mid / senior / lead. The most frequently mis-stated field in any
   posting, so the last one to trust and the one most likely to need a confidence floor.

### 6z — job fields, discovered from what actually found the job (added 2026-08-04)

**The owner's design, and it is better than the classifier 6w first proposed.** Stage 2.

We already search every board with a set of keywords. A posting that turns up under *both* "python
developer" and "backend engineer" has told us those two terms describe the same kind of work — not
because a model guessed, but because a board returned the same row for both. Do that across
thousands of postings and the terms group themselves. **The clusters are discovered from evidence
rather than invented in advance**, which is the argument that killed the title-keyword filter:
observed behaviour beats a list somebody wrote down.

**1. Record which query found which row.** Nothing does today — `corpus` carries a `source` and no
query. One join table (`job_id`, `keyword`) written at ingest. **This is the enabling change, it is
small, and it is worth doing on its own**: it is also the missing input for the per-keyword yield
history in 6o.2, which cannot be built without it.

**2. Group the keywords that keep landing on the same postings.** Terms that co-occur across many
rows belong together; each group gets a stable label. A job's `job_field` is the group its keywords
fall in. A job found by one keyword still gets that keyword's group.

**3. Match a user to their nearest cluster, not to their literal terms.** The owner's point, and it
is what makes this a filter rather than a keyword match by another name: a user's field is the
cluster their keywords sit closest to, so somebody searching "backend developer" is matched to the
whole software cluster and receives the "Vehicle Dynamics Specialist"-shaped jobs their own wording
would never have found. That is the property 6o.1 demanded and a keyword prefilter could not give.

**4. The jobs no query found — open, and the real gap.** ATS boards and feed-shaped sources return
postings nobody searched for: **1,720 rows in the last seven days from the ATS family alone**, and
they arrive with no keyword attached, so steps 1–3 give them nothing. Today they are simply scored
by every user's model like everything else, which is exactly the cost this work exists to remove.
Three candidate answers, none yet chosen:

- **Nearest neighbours in the embedding.** Every row already has a vector. A posting sitting among a
  hundred rows labelled *engineering* is engineering, with a distance threshold below which it stays
  unlabelled. Cheapest, uses only what exists, and inherits whatever the labelled rows got right.
- **Label the ATS company, not the job.** An ATS token belongs to one employer, and employers are
  usually in one or two fields. Once a company's queried jobs are labelled, its unqueried ones
  inherit. Cheap and probably accurate for small companies, wrong for conglomerates.
- **Ask the model once per row.** Accurate, and the only option that costs money per posting. It is
  a one-off per job rather than per user, which is not nothing, but it reintroduces the per-row LLM
  cost this whole phase is trying to delete.

**The cold-start limit is real and worth stating.** The label space is shaped by the terms our users
actually search, so a field nobody has signed up for produces no cluster. By the rule in 6w that
means those jobs are unfiltered rather than hidden, so the failure is a cost rather than a loss.
Operator keywords (6n.2) are the lever that seeds a field ahead of the first person who needs it,
and this is a second reason they matter.

### 6y — the corpus grows forever, because the sweep that stops it is never called (added 2026-08-04)

`trim_cold` drops the description and vector from unpinned rows older than `CORPUS_HOT_DAYS` and
keeps the ~258-byte row so dedup still works. It is implemented, tested, and **called by nothing** —
a third dead sweep beside the two in 6c, and the one with a disk bill attached.

Measured on the live box: **17,089 rows, 0 ever trimmed, 136 MB.** Descriptions are essentially all
of that, at roughly 8 KB a row. `PLATFORM_ARCHITECTURE.md` §4 projects 400k rows at 0.87 GB steady
state, and that projection *assumes trimming*. Without it the same 400k rows is nearer 3 GB, and it
never stops.

`user_job_state` is the other growth term and it is per user: 34,036 rows for two accounts, one per
job each has been scored. At fifty accounts that is a row per account per corpus row. **The country
and field filters in 6u and 6w cut this directly**, because a row that is never scored is never
written — which is a second reason they are worth having beyond the CPU.

**Archived, not deleted** (owner's call, 2026-08-04). `trim_cold` as written *destroys* the
description and the vector. That is the wrong trade now: the text is the only record of what a job
actually said, and every future idea in this phase — a better field classifier, a salary parser, a
retrained embedding, an A/B against old postings — wants a corpus of historical text to work
against. Deleting it makes those experiments impossible rather than expensive.

So the sweep becomes a **move**: description and vector leave the hot table after
`CORPUS_HOT_DAYS`, the ~258-byte row stays so dedup still works, and the text goes somewhere cold
and cheap. Open, and to decide when it is built: a second Postgres table on the same box is the
simplest thing that works and does not solve the disk problem; object storage off the box does, and
adds a dependency this platform has so far avoided. The cost difference at 3 GB is small enough
that "simplest" probably wins for now and the interface should make the other swap-in-able.

Wants a caller on a schedule, like 6c. Not urgent at 136 MB; not something to discover at 30 GB.

### 6v — every limit an operator can see and change (added 2026-08-04)

**The rule: a number that governs how hard we hit a source belongs beside the other one that does.**
The per-source freshness window is already an operator setting with a control in the admin panel;
these are the same kind of number and are currently constants, so tuning one means a deploy.
Documentation and rate limits change over time, and the person who notices is the operator.

Moving into platform settings, per source, rendered next to the freshness column:

1. **Queries per cycle, per source** — replacing `HARVEST_MAX_QUERIES_PER_CYCLE = 60`. That number
   was sized for the JobSpy tier's ban risk and now governs unproxied REST boards, where it means
   nothing (6r). Per source, it means what it says: this is how many times a cycle we are willing
   to ask this board. JobSpy/LinkedIn keeps a tight one for the reason the global number existed.
2. **Results per query, per source** — today a scatter of adapter constants: `limit: 100` for
   Remotive and Himalayas, `pageSize: 100` for NoFluffJobs, three pages of 40 for HiringCafe,
   `count: 50` for Jobicy. Each is a real ceiling on how deep one query reaches, and each is
   currently invisible.
3. **ATS companies per cycle**, with **the total we hold shown beside it.** `max_companies: 400` is
   a constant, and the token list grows on its own from harvested apply-URLs — so an operator
   choosing that number needs to know whether it is 400 out of 500 or 400 out of 12,000. The count
   is a query nobody currently runs.

**Deleted rather than moved: `request_delay_s`.** It exists on two adapters, was applied by one, and
the owner's call is that pacing is not something we should be doing by hand. If a board refuses a
burst, that is a per-source query ceiling to lower, which is item 1, and it is visible where the
delay never was.

Keywords per user and locations per user stay where they are — they bound what one person can ask
for rather than what we do to a source, and the config form already shows both.

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
4. ~~**Managed Agents is beta and sits on the onboarding path.**~~ **Retired 2026-07-28** — plain
   Messages calls, no beta on the onboarding path.
3. **Being your own first user (Phase 1 exit) is the cheapest bug-finding available.** Resist
   onboarding a second person early.
