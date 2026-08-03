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

### 6h — prove the setup chat, end to end ⬅ **next**

The owner's call, and the right one: everything downstream is grounded on files the setup agent
writes, and that agent has never been watched through a complete run on a fresh account. The
evidence that it needs watching is already in production — one account finished with four of eight
setup files written, four missing, and **trained a model anyway** (now blocked, 6i).

1. **`/setup` as the entry point.** First visit shows the chat disabled behind a single **Start**
   button that submits `/setup` for the user. A blank chat box asking a new user to invent the first
   message is the least helpful screen the product has; one button that begins a guided run is the
   most helpful.
2. **An overseer, not a free-running agent.** Something must own the sequence: run each setup agent,
   check its file landed and is filled, then move to the next. The failure to design out is the one
   already seen — a run that completes 90% and stops, leaving no error and a half-built account.
   The completeness predicate now exists (`libs.profiles.missing_setup`), so the overseer has a
   definition of done to check against rather than a vibe.
3. **Watch a real run on a fresh account**, and only then judge the prompts.

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

### 6j — the chat should look like a conversation (added 2026-07-31)

Owner's design note, deliberately kept small: each message in its own container, the user's on the
right and the model's on the left, with a rail of dots down the side showing what the agent is
doing — the shape Claude Code uses for tool calls. **Identical whether the account is on OAuth or an
API key**: the transport is not a thing a user should be able to infer from the layout. Not a
redesign, just the containers, the sides, and the activity rail.

### 6k — the chat surface, after the setup flow works (added 2026-07-31)

Deliberately queued *behind* 6h: polishing a flow that cannot complete is the wrong order. Recorded
here so it is not lost once setup runs end to end.

1. **Per-step containers with a dot rail.** One container per step of the agent's reply rather than
   one block per turn, with a dot on a vertical line down the left — the shape a coding agent uses.
   A reply that alternates one line, several lines, one line currently reads as one undifferentiated
   wall.
2. **A processing indicator in the bottom-left**, so a user watching a long turn can tell the
   difference between working and stuck without scrolling to find the caret.
3. **Stop.** A running turn cannot currently be cancelled. It is queued work on a worker, so the
   stop is a state flip the worker checks between tool steps rather than a killed request.
4. **Model version history**, for the user on the Model page and for an operator on a per-user admin
   page reached by clicking a user in the list. Every version with its head, CV-AP, test-AP, tuned
   cutoff and out-of-fold precision/recall — the data already exists in `models.metrics`, and today
   the only way to see v2 → v3 → v4 improve was to run SQL by hand.

### 6l — one fetch per job, one cadence for everyone (decided 2026-08-01) ⬅ **next**

**The premise: this app streams the newest postings.** Everything below follows from taking that
literally rather than making it configurable.

**1. LinkedIn fetches the whole posting on first sight.** Today the link pass stores a *snippet*,
the row is embedded and scored on that, and an hourly `linkedin-describe` replaces it with the real
JD — which invalidates the vector and triggers a rescore. That self-heal is not a safety net, it is
a wrong score that exists for up to an hour and a job that may have been silently rejected on a
preview nobody re-examines.

The fix is the owner's, and it is cheaper than either alternative: **links → dedup → fetch the full
JD only for the rows that are new → store complete → embed → score.** The objection to inline
fetching was that the same posting would be fetched once per interested user; dedup *before* the
fetch removes it, because a job already in the corpus is never fetched again by anyone. One proxy
hit per new job, not per user, and not per user per hour.

Asking jobspy for descriptions directly (`linkedin_fetch_description: True`) is **not** the way to
do it — it would fetch every result including the ones already held. The dedup has to sit between
the listing and the description fetch.

`linkedin-describe.timer` is deleted with it.

**2. Freshness becomes admin-set, per source.** Sites differ in how fast they list and how far back
their filters reach, so the value is per source and set once by the operator — not per user. The
per-user field is dropped, not merely unread.

**3. One delivery cadence: 15 minutes, for everyone.** `users.notify_interval_min` goes. A user
choosing "every 4 hours" is a user asking to be told about a two-hour-old job, which contradicts
the product. The only thing left that a user tunes is their keywords and locations.

**4. Latency is a requirement, not a side effect.** Found → described → embedded → scored →
delivered has to complete quickly enough that a posting inside a 4-hour window still arrives with
most of that window left. Worth measuring end to end rather than assuming: the stages are already
timed (`libs.obs.timed`), so the number exists once somebody reads it.

**Open:** what happens to a row whose JD fetch fails permanently — never scored, or scored on what
little there is after N attempts. Not yet decided.

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

1. **The 98 imported applications — decided 2026-07-31: do it.** Real applications the founder made,
   sitting as `applied_at` timestamps that never became training data. 98 true positives at 5×
   weight against a pool of 3,498 LLM guesses is the single largest quality win available. The
   argument against was that they predate the current profile generation; the owner's answer is that
   an application is the strongest signal the platform will ever get about what he actually wants,
   and a rubric edit does not make it untrue. **A person applying to a job is ground truth; an LLM
   scoring it is an estimate.** Backfill writes `source="feedback"` at `FEEDBACK_WEIGHT`, stamped at
   the current generation, and the next retrain consumes them.
2. **Prove one live tap**, end to end: tap Build CV in Telegram → a `feedback` label appears →
   a retrain consumes it. Until that has happened once, the flywheel is untested in production.

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

### 6n — the corpus an operator can see and seed (added 2026-08-03)

**Why.** A new user's training data is whatever the shared corpus already holds for their field, and
the corpus only holds what previous users searched for. The second account on the platform found 240
matching postings against 14,359 rows, labelled ten of them, and stalled — not through any bug, but
because the corpus had accumulated for weeks against one software search and hours against a
mechanical engineering one. Every user after the first inherits that shape.

Four pieces, in this order. The first two are additive and safe; the fourth can silently cost people
jobs and must not be built before it can be measured.

**1. A corpus browser in the admin panel.** Search across every posting, without scores — scores are
per user and this is the shared table. Source and date facets, so "what is actually in here" is a
question an operator can answer. Today the only way to look is SQL.

**2. Operator keywords.** Terms an operator injects into the query set to fill the corpus ahead of
the users who will need it. **Stored separately from user keywords and visibly distinguished**: an
operator may delete their own and must not be able to delete somebody's real search term, which is
theirs and is the thing their feed depends on.

**3. Latency thresholds in the admin panel.** `pipeline_latency()` already measures found → scored →
delivered; what is missing is green/amber/red against thresholds so a number nobody reads becomes an
alert somebody acts on. **Set from measured normal behaviour, not guessed** — this has already gone
wrong once, when the found → scored median looked alarming and turned out to be measuring backlog
age rather than pipeline latency. Colours nobody trusts are worse than no colours.

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
