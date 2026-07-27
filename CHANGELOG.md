# Changelog

What changed, where, and **why** — the last one is the point. A diff already records what changed;
this file exists so that six months from now the reasoning behind a decision is recoverable without
archaeology.

Conventions (carried from the reference instance, `docs/saas/CODE_STRUCTURE.md` §7):

- **Non-trivial change → an entry here.** Formatting and typo fixes do not need one.
- **Major change → update the relevant doc in the same commit**, and say so in the entry.
- Newest first. Dates are the day the work landed.
- Per-folder state lives in that folder's `README.md` under **Current state**; this file is history,
  those are position. Deliberately not duplicated — two copies of the same fact drift.

---

## 2026-07-27 (later)

### Phase 2 step 0 — `libs/secrets`: envelope encryption, and T4 in the same commit

Phase 2's first commit, and **not** the order `ROADMAP.md` lists. The roadmap puts the secrets
module at step 6, after profile capture — but `PROFILE_CAPTURE_PLAN.md` §4.1 says capture runs on
the user's own Claude credential, and `TENANT_SAFETY.md` §5's onboarding order is
`credentials → capture`. Building capture first would mean holding a stranger's live API key with
no encryption behind it, which is exactly what §2 exists to prevent. Secrets first. It also unblocks
the proxy credential, which the LinkedIn loop already needs and currently has nowhere real to live.

**The design, three files:**

- `keyring.py` — AES-256-GCM. KEK from `SECRETS_KEK`, per-user DEK wrapped by it, secret sealed by
  the DEK. Two decisions worth naming:
  - **Owner and kind are bound in as associated data.** Without it, an attacker with database
    *write* access (not key access) can copy user A's ciphertext onto user B's row, or relabel a
    proxy password as a Claude key, and both decrypt silently. With it, both fail closed.
  - **A wrapped DEK carries the id of the KEK that wrapped it.** Two bytes now; unrecoverable
    later. Rotation becomes re-wrapping one small column per user — no secret is re-encrypted —
    and a retired key keeps reading old rows until `users_needing_rewrap()` comes back empty.
- `store.py` — the database layer. `user_id` required on every method, which here is load-bearing
  twice: it is also the AAD. `put()` overwrites rather than versions, because versioning keeps a
  revoked credential decryptable. `purge()` deletes the rows **and** clears the DEK — crypto-
  shredding, so "destroyed on account deletion" is true across the backups too, not just live.
- `redaction.py` — two layers, because neither is enough alone. A `Secret` type whose `str`,
  `repr`, and f-string all render `<secret:claude_api …ab12>`, so the accidental-interpolation path
  is closed *at the type level*; plus a logging filter as the backstop for values that reach a log
  through code we do not own.

**Two things that would have been silently wrong:**

1. **A redaction filter on the root logger protects almost nothing.** A logger's filters run only
   for records logged *on that logger* — a record from `logging.getLogger("services.notifier")`
   propagates straight to the root's *handlers* without consulting the root's filters. So `install()`
   attaches to the logger **and to each of its handlers**, and a test logs from a child logger
   specifically to prove it.
2. **Tracebacks are formatted after filters run.** Scrubbing `record.msg` alone leaves the
   credential in the traceback — which is precisely where a failed proxied fetch puts it, since the
   connection error quotes the URL it dialled, userinfo and all. The filter renders and redacts the
   traceback itself into `record.exc_text`, which `logging.Formatter` then uses instead of
   re-rendering.

**T4, same commit** (`tests/test_secrets.py`, 42 tests). The headline assertion is made the way a
thief would experience it: dump every column of every row belonging to a user and search the lot for
the plaintext — not "the ciphertext column differs", *nothing*. Plus: a different KEK cannot read
what this one wrote; no rendering of a `Secret` shows the value; a known secret cannot appear in a
log line, a child logger's line, an object argument, or a traceback; and the API read path exposes
`last_4` and timestamps with no field that could ever carry the value.

Test values are shaped like the real thing (`sk-ant-…`, a `\d+:…` bot token, a proxy URL with
userinfo) because the pattern half of the filter recognises credential shapes — a `hunter2`
placeholder would pass tests the real value fails.

**Gaps recorded, not closed:**

- **Save-time validation is a seam, not an implementation.** `TENANT_SAFETY.md` §2 wants a real
  minimal API call before a credential is accepted. `put(..., validated=False)` and
  `mark_validated()` exist; the callers that make the call arrive with capture (Claude) and the
  notifier (Telegram). This layer deliberately does no network I/O, so saving a credential cannot
  be blocked by a third party being down.
- **Nothing calls `install()` yet.** The harvester's entrypoint gains it when the LinkedIn loop
  starts decrypting proxy credentials; until then no service holds a plaintext.
- **`SECRETS_KEK` is not yet set on the platform box.** Documented in `deploy/.env.example`,
  including why the first rotation is a config change rather than a migration.

317 tests. `SECRETS_KEK` and `cryptography>=44.0` added to the deploy env and dev requirements.

## 2026-07-27

### Phase 1 exit — the harvester runs against live sources

The cycle deployed and run on the platform box. **193 links, 185 new, 179 embedded, 0 source
failures**, and a second cycle immediately after found **193 links, 193 duplicates, 0 new** — dedup
holds across runs.

Corpus state after: 193 rows, 187 with text, **187 embedded**, 5 sources contributing. Vectors are
exactly 3,072 bytes (768 x float32) carrying `BAAI/bge-base-en-v1.5|dim=768|norm=1` — the 36-char
version string that would not fit the original `String(32)`.

**Three bugs, all found by the first contact with real data.** Every one was silent.

1. **Three sources made no HTTP request at all.** `remotive`, `himalayas` and `arbeitnow` iterate
   `cfg["queries"]` and return `[]` when it is absent. No request, no log, and the cycle reported
   success — because an adapter returning `[]` is the registry's contract. The same property that
   stops one dead source sinking a run also hides a misconfigured one.
2. **The recency window was 24h, not 72h.** The adapters default internally to 24 while the config
   schema's own default is 72, so the first cycle fetched a much narrower slice than intended — 8
   links where there should have been ~190.
3. **`content_key` was `String(64)`.** It is a readable composite (`company|title|location`), not a
   hash, so its length is a function of a job title somebody else wrote. A German posting produced
   85 characters and every ingest died. Third instance of the same mistake this session, after
   `embedding_version` and `models.embedder_model` — bounding a string whose length is not ours to
   bound. Every remaining `String(n)` column was audited; the rest are hashes, enums, or
   RFC-bounded.

**A fourth bug hid the third.** The advisory-lock release ran in a `finally` on an already-aborted
transaction, raised `InFailedSqlTransaction`, and propagated *instead of* the `DataError` that
caused it — so the log showed a lock problem and hid a schema one. Cleanup must never mask the
failure that caused it.

**Two sources return zero, and that is correct.** `weworkremotely`'s feed has 25 items whose newest
is 26 days old; `remotive` returns 36 jobs, all outside the window. The 72-hour filter is doing its
job. Recorded so nobody "fixes" it into admitting stale postings.

275 tests passing. **Phase 1 is functionally complete.**

### The harvest cycle - one-shot, locked, timer-driven

`services/harvester/cycle.py` and `__main__.py`. The last Phase 1 piece: the loop that runs the
components in order - demand -> budget -> sources -> dedup -> upsert -> embed.

**A one-shot command rather than a daemon with a sleep**, which was the open question. Four reasons,
in order of weight:

1. **Restart safety comes free.** A crashed one-shot is a run that did not happen and the next tick
   starts clean. A long-lived loop that dies stops harvesting *silently* until somebody notices -
   the failure class `OPERATIONS_PLAN.md` §4 exists to prevent.
2. **It composes with Phase 2 instead of being replaced.** `run_cycle` becomes the body of a queue
   worker; an internal timer would be thrown away.
3. **It is testable.** `TEST_STRATEGY.md` §2.1 forbids `sleep` in tests, which rules out testing a
   loop containing one.
4. **Each run is its own log line and its own exit code.**

The cost is overlap: a timer can fire while the previous run is still going. Handled with a Postgres
advisory lock **inside the cycle** rather than in systemd, so it behaves identically when Phase 2
moves this into the queue. `pg_try_advisory_lock`, not the blocking form - a tick that arrives late
should skip, not queue behind its predecessor and turn one slow cycle into a backlog all doing the
same work while each spends the full fan-out budget.

**A skipped tick exits 0.** With a 15-minute timer and an occasionally slower cycle, treating
overlap as failure would page somebody about the system working correctly.

`Persistent=false` on the timer, deliberately: catching up missed runs after downtime would fire
several cycles back to back against sources that are already grey-area. A missed harvest is simply a
harvest that did not happen.

**Universal cadence, and that is not a simplification.** There is no such thing as harvesting *for*
one user when the corpus is shared - one fetch serves everyone, so a per-user harvest interval would
mean N cycles fetching identical bytes, which is the design this architecture rejected. What users
choose is how often they are *told* (`notify_interval_min`) and how often their own paid LinkedIn
tier runs (`linkedin_interval_min`). Universal where the cost is shared, per-user where it is
personal.

Fails closed on a missing `EMBEDDER_URL` (exit 2) rather than harvesting rows nothing can embed -
they would sit unembedded and unscoreable while the cycle reported success.

265 tests passing.

### G11 and G12 closed, and a hole found in the drift check

**G11 (account lifecycle) was brought forward out of Phase 3**, because its stated cost is a Phase 1
one: "dormant users consume harvest budget and run their own LinkedIn tier against their own proxy
indefinitely". The harvest fold being built next is exactly what has to exclude them.

**Implemented against the product rather than against the sentence.** G11 defines dormant as "no
login in N days". Delivery is Telegram — a user who reads their feed every morning and applies to
things may never open the web app again after onboarding, so login-only would pause the feeds of the
most engaged users and tell nobody. `last_active_at` is bumped by login **or** any Telegram action.
30 days, reversible instantly, and `suspended` is deliberately not reversed by activity: dormancy is
a cost control, suspension is a decision.

**G12 (artifact growth)** — `prune_model_artifacts` keeps current plus one previous. It returns the
paths rather than deleting files: the repository owns rows, not the filesystem, and splitting it
means a rolled-back transaction cannot leave rows and files disagreeing about which versions exist.

**The find that matters: Alembic autogenerate does not detect `CHECK` constraint changes, and
neither does the CI drift check, because it is built on autogenerate.** Adding `dormant` to
`USER_STATUSES` produced a migration that added the column, left the constraint listing the old four
values, and passed the drift check — while every write of the new status failed with an integrity
error. Recorded in `libs/db/README.md` alongside the other silent failure mode (a model not imported
by `models/__init__.py`), because neither is discoverable from a green build.

A second, smaller trap in the same fix: `op.drop_constraint` takes the **logical** name
(`status_valid`), not the rendered one — the naming convention expands it, so passing the rendered
name produces `ck_users_ck_users_status_valid`.

**G9 (cost visibility) stays open** and is genuinely Phase 3: it meters Claude spend, and nothing
calls Claude yet. The one part worth taking early — integer units rather than floats for anything
billed — is already the precedent (`proxy_credentials.mb_used_today` is `Numeric`, not `Float`).

255 tests passing.

### Step 5 - embed once, for everyone, ever (T8)

`libs/contracts/embedder.py` and `services/harvester/embedding.py`. A vector is a deterministic
function of the text, so embedding the same posting for fifty users is fifty times the CPU for
byte-identical output. Putting it once on the shared corpus row is the whole scaling argument.

**A regression here is invisible until the CPU bill** - nothing errors, feeds populate, scores look
reasonable. So T8 asserts the backend call count through a counting stub rather than trusting the
structure.

**A real schema bug, and it would have failed on the very first production embed.**
`corpus.embedding_version` was `String(32)`. The live embedder's actual version string is
`BAAI/bge-base-en-v1.5|dim=768|norm=1` - **36 characters**. Every embed would have failed with a
string-truncation error. It surfaced only because the test used the real format rather than a short
placeholder, which is an argument for fixtures that look like production. `models.embedder_version`
had the same bound and the same bug. Both are `Text` now: the version is a composite of everything
defining the vector space, and its length is a function of the model name, which is not ours to
bound. Migration `6f4ca35c9cb4`.

Three properties the tests pin, each silent if lost:

- **Compatibility is asserted before anything is written.** A vector from the wrong model is not a
  worse vector, it is a meaningless one, and nothing downstream catches it - the dimensions match
  and the numbers look like numbers.
- **The digest is re-checked at write time.** A cold-start batch takes minutes while `refresh.py`
  mutates descriptions; a vector whose `text_sha256` no longer matches is refused and the row is
  picked up next pass.
- **The bulk lane is used, not the fast lane.** They are separate worker pools, not a priority flag,
  because a score request otherwise waits behind an in-flight bulk call - measured at 0.08s idle
  versus 8-15s with a shared pool.

Also refused rather than guessed: a short vector list (fewer vectors than texts means the mapping is
unknown, and guessing attaches one job's vector to another job's text), and a gateway 503.

247 tests passing. **Phase 1 is complete.**

### Step 4c - the source adapters, ported as a scripted transformation

~3,500 lines lifted from `pipeline/sources/`, plus `libs/corpus/filters/timefilter.py`. Done by
`services/harvester/sources/port_from_reference.py`, which is committed alongside them: the
transformation is the interesting part of the port, and re-deriving it later from a diff would be
far harder than reading it. Retyping the adapters by hand would have introduced transcription errors
into code whose correctness depends on details no test here can check - a JSON path, a date format,
a pagination parameter.

**4c was not a pure lift, and the reason matters.** Every adapter built its own `httpx.Client` - 17
call sites. Harmless single-tenant, because none of those boards need a proxy. Here it would have
broken the invariant `fetch.py` exists to hold **and** restored `trust_env=True` on all 17, so an
ambient `HTTP_PROXY` could route the whole shared harvest through one user's credential. There is
now a test asserting no module under `services/harvester` constructs a client directly.

**Two real bugs found during the port:**

1. **The transformation silently dropped RemoteOK's custom User-Agent.** The reference sets one with
   the comment "RemoteOK blocks the generic Mozilla UA; a slightly more specific one works" - and
   my regex matched `headers=ua` and discarded it. The source would have returned nothing, with the
   explanation sitting two lines above. Caught by ruff flagging the now-unused variable, which is
   luck rather than design, so the rule is narrow now: only `headers=_UA`, the platform default the
   factory already supplies, may be dropped.
2. **`gather_linkedin` took its proxy from a config dict.** Correct with one tenant, wrong with
   many: a credential arriving as a dictionary key has no verified owner. It now requires a
   `ProxyLease`, keyword-only and mandatory. jobspy opens its own connections and cannot be routed
   through `make_client`, so this is the one place a proxy URL reaches a third-party library - which
   is exactly why the type carries its owner.

**One deliberate behaviour change: XML parsing now uses `defusedxml`.** Several adapters parse
RSS/XML from third-party boards with stdlib ElementTree, which the stdlib's own security docs flag
as vulnerable to entity-expansion bombs. Single-tenant on a laptop that is a shrug; here the
harvester shares a box with Postgres, and a hostile or merely broken feed exhausting memory takes
the platform down for everyone.

The registry now records **which tier a source belongs to**, so the shared loop cannot be handed the
proxied one - the same guarantee `make_shared_client()` gives at the HTTP layer, stated twice
because it is what D3 rests on. LinkedIn is the only per-user source, and a test asserts it.

Adapter style findings are ignored per-file with reasons, as for the embedder: restyling lifted code
would destroy the diffability that justifies a scripted port. **F841 stays on** - it is what caught
the User-Agent regression.

233 tests passing.

### Step 4b - the fetch layer, where D3 stops being a policy and becomes structure

`services/harvester/fetch.py` and `proxy_usage.py`. Decision D3 says a user's proxy credential is
used strictly for their own fetches, never pooled. That is a claim about *every* outbound request the
harvester makes, so it can only hold if there is exactly one way to make one - `make_client()` is
that way.

Three departures from the reference, all forced by multi-tenancy:

- **The proxy comes from a lease, never the environment.** The reference reads `PROXY_URL` from env,
  which is single-tenant by construction. There is no env fallback here on purpose.
- **A client carries the id of the user it was built for**, so "was this credential used for someone
  else's fetch" is an assertion rather than a code review.
- **`PROXY_HOSTS=*` is gone.** Under per-user billing, proxy-everything would spend one user's
  metered bandwidth on shared ATS fetches that work fine direct.

`make_shared_client()` takes no lease parameter at all, so the shared loop cannot spend anyone's
bandwidth even by mistake. Structural, not conventional.

**A real bug the tests caught: `trust_env`.** The shared client set no mounts, so httpx defaulted to
`trust_env=True` and would have honoured an ambient `HTTP_PROXY` - silently routing every user's
shared traffic through one endpoint. That is the pooling D3 forbids, arriving through an environment
variable nobody set deliberately, and it would have looked like it was working: jobs arrive, feeds
populate, and the only symptom is one person's proxy bill. Now `trust_env=False` everywhere, with a
test that sets the env vars and asserts they cannot leak in.

**A second bug: the daily window.** `roll_daily_window` treated a null `usage_reset_at` as "the day
has turned" and zeroed the counter, so the first lease of any credential handed back a spent cap.
Null is initialisation, not a day boundary. Fixed, and the timezone case is now tested in both
directions - a user east of UTC must reset on *their* midnight and must not reset on UTC's.

### T13 and T1 closed

**T13 is done, both halves.** The visibility half was already proven; the cross-use half - the more
important one - now is: owner identity on the client, LinkedIn-only routing, redirects out of
LinkedIn going direct, ambient proxy rejected, metering on every response including failures (the
proxy bills on the wire, not on success), and quarantine on *consecutive* failures rather than
cumulative, which would eventually quarantine every healthy user.

**T1 is complete for every read path that exists.** Added config, secrets metadata and queue rows to
the isolation coverage. Documents remain uncovered because `docgen` does not exist yet - that is
named in the status table rather than left as an unexplained amber.

Secrets have a metadata-only read path by design: `last_4` and timestamps, never ciphertext. There is
deliberately no repository method that returns a secret value; the worker that needs a plaintext
decrypts it in memory for one call and that path does not go through the repository.

213 tests passing.

### Audit against the specs, and T9

Checked what is built against `TEST_STRATEGY.md` and `GAPS_REVIEW.md` rather than against a feeling
that things are on track. Status is now recorded next to each spec — no fourth place tracking state.

**The audit found a real miss: T9 had no tests.** `libs/contracts/config.py` shipped in the previous
commit and the config schema was never tested, despite T9 scoring 7/10 as "primary onboarding
failure mode". `TEST_STRATEGY.md` §4's ordering rule exists precisely to stop that; it was followed
for T2 and T1 and not here. Now 34 tests, including the seam where a *validated* config actually
drives the harvester — testing the schema and the query-set builder separately would have left the
join between them unproven, and that join is where a renamed field silently produces zero queries.

**Writing that seam test found a second problem: the per-user keyword cap was defined twice** — a
literal in the schema and a constant in `services/harvester`. Two definitions of one rule is how
limits drift: raise one and the other silently accepts terms it will never serve. The first instinct
was to write a test asserting the two numbers agree; the right fix was to remove the duplication.
It moved to `libs/contracts`, which is the **only** package both may import from, since `libs` must
never import a service. At Phase 3 it becomes a row in `plans.limits` and the seam is unchanged.

Honest status carried into the docs:

- **T1 is partial, not done.** Scores, labels, applied, and cascade completeness are covered;
  config, secrets, queue, and documents are not, because those read paths do not exist yet. It is
  the release blocker and must be finished before launch.
- **T13 is half done.** The visibility half is proven — no healthy credential, no LinkedIn rows,
  per-user on one shared row. The "credential never cross-used" half needs the fetch layer, and is
  the more important half.
- **G5, G7, G14 closed. G15 and G16 are policy-and-schema built, execution deferred** to the queue
  (Phase 2) and step 5 respectively.

Anti-flake compliance re-checked against §2: no `sleep`, no real network, every database test in a
rolled-back transaction, seeded or injectable RNG wherever anything samples, no wall-clock
assertions, real Postgres 17 rather than SQLite.

Infrastructure: **4 GB swap is live on the VPS** (`swappiness=10`, persistent in fstab), which
closes the risk raised when TEI's CPU ceiling was raised on a box where memory contention was an
OOM-kill rather than a slowdown.

173 tests passing.

### Step 4a - the harvester's governor, before the harvester

Phase 1 step 4, split: the deterministic half now, the source adapters as 4b. Same fault line that
made step 3 tractable - build the part whose correctness is provable without a network first, and in
this case build the thing that *limits* scraping before the thing that scrapes.

- **`libs/contracts/config.py`** - the bounded user config. Invalid states are unrepresentable
  rather than validated away: controlled vocabularies, 1-5 keywords, bounded salary and recency.
  Locations are a picklist and that is not only a UI choice - harvest dedup folds on a normalised
  location, so free text would multiply the query set with spellings of the same place.
- **`services/harvester/query_set.py`** - QueryKey normalisation, folding, fan-out budget,
  round-robin selection.
- **`services/harvester/governor.py`** - the LinkedIn tier's global controls.
- **`proxy_credentials`** table, and the visibility gate in `libs/tenant`.

**A bug the tests caught in my own implementation.** `HARVESTER_PLAN.md` section 2 specifies the age
bucket as *"rounded up to the coarsest bucket any requesting user asked for"*, so a 24h user and a
72h user share one 72h fetch. I had bucketed each user's value independently and folded on it, which
*fragments* demand instead of merging it - two fetches whose results are strict subsets of one
another, exactly the duplicate work the budget exists to prevent. Age is now resolved per group
after folding.

Three more failures in that run were bugs in the tests, not the code: I had given every user
identical keywords, so folding correctly collapsed them and the scenarios I meant to build never
existed.

**Round-robin is the load-bearing choice in selection.** Ordering by demand would serve popular
terms repeatedly and starve anyone with an unusual search - precisely the user for whom this product
is most valuable. Deferred queries are served first next cycle so an unlucky query does not stay
unlucky.

**The visibility gate makes free-riding impossible by construction.** LinkedIn rows live in the
shared corpus so dedup and embed-once still work, but a user sees them only while their own proxy
credential is healthy. Applied as a `WHERE` clause, not a post-filter - filtering after `LIMIT`
would hand short pages to exactly the users who have no credential.

**The per-user tier still needs a global governor** (section 5.2b). Per-user proxies give IP
diversity and handle ban risk; they do nothing about the platform issuing hundreds of concurrent
requests, or a herd forming when users share an interval. Hence a concurrency ceiling, jitter
applied per scheduling decision (not once at signup, or a restart re-converges everyone), and a
kill switch - the alternative during an incident is editing 50 users' configs while LinkedIn blocks.

Execution is deliberately not wired: the per-user loop is an ordinary queue job, and `libs/queue` is
Phase 2. Nothing half-built runs.

### CI now lints

`ruff check .` added as a step, after noticing ruff had only ever been run by hand - which means it
had only ever run on the files someone remembered to name. Rules are selected explicitly rather than
inherited from ruff's defaults, which change between releases. `services/embedder` carries per-file
ignores with reasons: its findings are deliberate choices in deployed, gate-tested code, and one
flagged line (`bulk_s == bulk_s`) is an intentional NaN check that a "fix" would have broken.

Migration `2f362ab1e0c5`. 140 tests passing.

### Step 3b — the remaining stores, plus a dedup layer step 3 had missed

**The important part is the gap, not the port.** Reading the reference's `processed` table for 3b
showed it indexes `app_canonical` and matches on `canonical = ? OR app_canonical = ?` — the resolved
**Apply URL**. Step 3's `find_duplicate` only compared `corpus.canonical_url`, so a LinkedIn posting
whose Apply button resolves to a Greenhouse board would have landed as a **second row** alongside the
one the ATS poll found directly. That is the precise case canonicalisation exists for, and it was
missing.

Fixed: `corpus.application_url` + `application_canonical_url`, and a fourth dedup layer checked in
**both directions** — the candidate's apply target against stored rows' own URLs, and the
candidate's own URL against stored rows' apply targets. Checking one direction only would leave half
the collisions undetected while looking like it worked.

The stores themselves:

| Reference | Here |
|---|---|
| `ats_tokens.json` | `ats_tokens` table, lowercased, dead boards retained not deleted |
| `ats_etags.json` + `.lock` | `ats_etags` table, **row-level upsert, no lock** |
| `applied.jsonl` | `user_job_state.applied_at` / `.cover_built` |
| `labels.jsonl` | `user_labels`, `source='llm'` |
| `feedback.jsonl` | `user_labels`, `source='feedback'`, `weight=5` |

**`ats_etags` is the one place the port is a fix rather than a translation.** The reference guards a
whole-map JSON rewrite with a file spinlock whose `_acquire_lock()` returns `False` on timeout and
lets the caller proceed anyway — so a contended write silently drops another writer's keys, and even
holding the lock the whole-map replace lets two ATS gathers clobber each other. Row-level upsert on
`(ats, token)` removes both by construction. Proven by an interleaved-write test, not asserted.

**Feedback turned out not to be a separate store** — it is a label with a different provenance and
weight. The reference keeps it apart and blends it at train time by duplicating rows five times so
the weighting survives sklearn Pipelines; recording `source` and `weight` on the row makes the blend
a query instead.

New package **`libs/tenant/`**, a deliberate addition to `CODE_STRUCTURE.md` §2's list. That list
predates the repository layer and has no home for per-user data access: `libs/corpus` documents
itself as the shared half, `libs/db` is where the schema lives. Putting tenant-scoped queries in
either would make the shared/per-user boundary invisible at the call site. Now it is visible in the
import path — `libs.corpus` takes no `user_id` at all, `libs.tenant` cannot be used without one.

**T1 landed** (`TEST_STRATEGY.md`, release blocker): two users, overlapping jobs, every read path
returns only the caller's rows — applied log, labels, and scores.

Also caught in review: `cover_built` was generated as `NOT NULL` with no server default, which fails
against any table that already has rows. It would have worked today only because the table is empty,
which is not a migration strategy. Added `server_default`.

Migration `fccd4d3a84ff`, applied to the live database (11 tables). 109 tests passing.

### Corpus repository — the storage half of the `store.py` port (Phase 1 step 3)

`libs/corpus/repository.py`, 14 behaviour tests, T2 green throughout. Dedup (url → canonical →
content_key, narrowing), ingest, description/embedding lifecycle, and the retention sweep.

**A schema correction the port surfaced.** The first draft made `corpus.canonical_url` unique and
`NOT NULL`. Both wrong, and only writing the repository exposed it:

- The reference keys `all_links` on **`url`** — `canonical` and `content_key` are *indexed, not
  unique*. Dedup is a query performed **before** insert, not a constraint. Making canonical unique
  looks tighter but converts a case the reference tolerates into a crash mid-harvest.
- Both derived keys must be **nullable**, because the key functions return `None` for input they
  cannot use. `NOT NULL` would make such a job impossible to store at all — silently dropping real
  postings rather than admitting them undeduplicated, which is the wrong direction to fail.

Migration `fe23e709fd46`, applied to the live database.

Two invariants the tests pin, both silent if broken: `first_seen_at` is never overwritten on
re-discovery (or a re-listed posting stays permanently fresh and un-trimmable), and a vector can
never outlive its text — changing a description clears the embedding, and `set_embedding()` refuses
a vector whose digest no longer matches. That last one is a real race, since bulk embedding takes
minutes while `refresh.py` runs concurrently, and the `(model, dim, version)` assert cannot catch it:
the vector space is right, the content is wrong.

Decided while building it: `CorpusRepository` takes no `user_id` at all (shared by construction,
per-user repositories are separate classes), and the reference's materialised `.load()` mode is
dropped — it was an artifact of the JSON store that preceded SQLite, and would mean an O(history)
memory pattern in a multi-tenant service.

### CI — three bugs that made the pipeline report success without checking

Verified every job by running it the way the workflow does, against a scratch database. Two of the
three made a check pass while checking nothing:

1. **pytest could not import `libs/` in CI.** The workflow calls the `pytest` console script; only
   `python -m pytest` puts the working directory on `sys.path`. Every test importing `libs.*` would
   have failed at collection in CI while passing locally — the worst split, because local green is
   exactly what gets checked before pushing. Fixed with `pythonpath = ["."]`.
2. **The drift check could never fail.** It globbed `*drift-check*.py`, which Alembic never writes —
   `file_template` slugifies the hyphen to `drift_check`. grep errored, the `if` read false, pass.
3. **The first rewrite still passed when Alembic itself failed** — a failed `generated=$(ls …)`
   assignment did not abort under `set -e`.

Proved the fix rather than assuming it: injected a canary column into the corpus model, confirmed
the check fails, reverted, confirmed green. A drift check that cannot fail is worse than none,
because it is also reassuring.

### T2 — the dedup regression suite, and the pure-function half of the `store.py` port

Phase 1 step 2. `libs/corpus/keys.py` is a faithful port of `canonical_url` and `content_key` from
`job-search/pipeline/lib/store.py`, with **66 tests** proving it.

**The port was split in two, and that is the whole reason T2 could come first.** The dedup
*semantics* are pure functions; the SQLite→Postgres move is *storage*. Separating them means T2 was
written and green before any storage work started — which is what the roadmap asks for and would
have been impossible had the two been ported together.

The proof has three layers:

1. **Golden cases** generated by *executing* the reference, not by reading its rules and writing
   down what they appear to say. A misreading transcribed into a test is confirmed by that test
   forever. Regeneration: `tests/golden/generate_dedup_golden.py`.
2. **Behavioural tests** restating each rule as intent, so the suite explains *why*.
3. **Differential fuzz** — 40,000 seeded random inputs through both implementations, **0
   divergences**. Skips where the reference repo is absent (including CI), which is why the goldens
   are committed rather than generated at test time.

**Found: the reference's own comment is wrong.** It claims `"Remote" == "Remote - EMEA" == ""`. It
does not — `remote` is stripped from the location but `EMEA` survives, so the two key differently
and do **not** collapse. Pinned by a test named after the discrepancy, because otherwise someone
eventually "fixes" the code to match the comment and silently changes what counts as a duplicate.

### Embedder deployed on the platform box

`services/embedder` now runs on `169.58.16.241` as the **platform's own instance**. The legacy
deployment serving `job-search` and `andras-job-search` lives on a different machine and was not
touched — decision D6 is parallel copies, decommission nothing until the platform provably matches.

Gate results, run in order per `services/embedder/README.md`:

| Gate | Result |
|---|---|
| 1 — backend-verified health | ✅ `verified: true` in ~45 s, `model=BAAI/bge-base-en-v1.5 dim=768` |
| 2 — full suite against the live stack | ✅ **23 passed, 1 skipped** (up from 15/9 with no stack) |
| 5 — priority lane under a training flood | ✅ included in gate 2 |
| 4 — peak RSS vs the 2 GB budget | ✅ **832 MB peak** against 2048 MB, 0 errors |

Gate 4 also showed `cpus: 1.5` on the TEI container was the binding constraint — a ceiling tuned for
the box that shared CPU with two single-tenant stacks, leaving ~6.5 of 8 vCPU idle here. Raised to
**4** and re-measured rather than assumed:

| | cpus 1.5 | cpus 4 | |
|---|---|---|---|
| Bulk cold start (2,000 texts) | 589.1 s | **296.7 s** | 1.99× faster |
| Score latency, median | 3.795 s | **1.586 s** | 2.39× faster |
| p95 | 4.862 s | **2.603 s** | 1.87× |
| max | 9.101 s | **5.158 s** | 1.76× |
| Peak RSS | 832 MB | **810 MB** | ↓ |
| Errors | 0 | 0 | |

**2.67× the CPU bought 2× the throughput.** Sub-linear, because TEI's CPU backend caps its internal
batch at 8 regardless of how many cores it has. Extrapolating, 4 → 6 would likely return under 1.3×
for 50% more contention — so **4 is the landing spot and going further is not worth it**. That
conclusion is the reason to record the numbers rather than just the change.

Peak RSS went *down* slightly, which is consistent: more CPU means less queuing, so fewer texts are
resident at once.

⚠ **Re-measure once the other services compete for the box.** This number is tuned to a machine that
is currently almost empty — precisely the condition that stops being true.

The single skip is `test_parity` (TEI vectors vs in-process `fastembed`). **That gate is moot for the
platform** and was skipped deliberately: parity exists to decide *match-or-retrain* for classifiers
that already exist, and the platform has none — every user's model will be trained on TEI vectors
from day one. It still matters for the legacy box, which is why the test stays in the suite.

Tests were run in a throwaway `python:3.12-slim` container attached to `embedder-net` rather than a
host venv. That needs no `sudo` (`python3-venv` is not installed) and, more usefully, exercises the
path consumers actually use — the gateway by service name, not the loopback host port.

### Postgres schema — eight tables, Alembic-managed (Phase 1 step 1)

`libs/db`. Applied to the live database and verified: re-running autogenerate produces **0
operations**, so models and database agree exactly.

Three decisions that are not transcriptions of the design docs:

- **Added `jobs.finished_at`.** `SCHEDULER_PLAN.md` §3's DDL omits it, but §5's fairness ordering
  selects `MAX(finished_at)` per user per lane. Round-robin does not work without it.
- **`job_id` foreign keys are `RESTRICT`, not `CASCADE`.** Written as CASCADE first, then corrected:
  cascading there would let a corpus retention sweep silently delete `applied_at`, which §4 calls
  the record a user would be angriest to lose. `user_id` still cascades, so account deletion stays
  one statement.
- **Config split by access pattern.** The four fields the 60-second dispatcher reads (`active`,
  `next_run_at`, `notify_interval_min`, `timezone`) are indexed columns on `users`; everything else
  the config form collects is JSONB on `user_config`.

Closes **G5** (Alembic from the first table, no hand-applied DDL ever) and **G7** (every timestamp is
`TIMESTAMPTZ`, so per-day counters can reset at the user's local midnight rather than UTC's).

`tests/test_schema.py` is the seed of **T1** — written now because the roadmap puts the isolation
test at the moment the first per-user table exists, not once there is a repository layer to test
through. CI runs it against a real Postgres 17 service, plus a drift check that fails if a model
changed without a migration.

### Postgres deployed

`deploy/compose.yml`. Loopback-only port, `${POSTGRES_PASSWORD:?}` so the stack refuses to start
without a password rather than defaulting to a guessable one, `mem_limit: 4g` because the box runs
no swap, `shm_size: 1g` because the 64 MB default makes parallel query workers fail with errors that
read like disk problems.

### Legal review cleared

GDPR posture, scraping ToS, and Anthropic's terms on subscription credentials in a hosted service
all confirmed cleared. The **implementation** obligations they imply are scheduled into Phase 3 —
see `docs/saas/ROADMAP.md`. A review passing and the obligations being built are different things.

## 2026-07-26

### Step 0b — capacity measured, D4 confirmed

23 GiB RAM with 22 GiB available, 8 vCPU, 382 GB free disk, ~337 MiB resident. Roughly 3× headroom
against the projection. Recorded in `PLATFORM_ARCHITECTURE.md` §11 along with the caveat that
matters more than the verdict: **neither single-tenant stack was on the box**, so the ~3.8 GB that
made the D6 arithmetic tight has not been paid yet.

Surfaced three follow-ups: the embedder needed standing up here (done 2026-07-27), the box has **no
swap**, and Postgres was not installed (done 2026-07-27).

### `embedder/` → `services/embedder/`

Its own commit, so a bisect lands on it cleanly. **15 passed / 9 skipped before and after,
identical** — the proof the move was inert.

Nothing relative needed editing: the compose build context, the Dockerfile `COPY` paths, and
`conftest.py`'s `sys.path` insert are all computed relative to files that moved together, and the
compose project name is pinned. Only things hardcoding a path from the repo root changed.
`CODE_STRUCTURE.md` §6 now records which surfaces those actually were, because the pre-move guess
was wrong in the direction that matters.

### Step 0a — repo layout and boundary enforcement

`libs/` and `services/` per `CODE_STRUCTURE.md` §2, as empty packages whose `__init__` docstrings
record why each exists. `import-linter` wired into CI on the first commit: **services may not import
each other**, **libs may not import services**. Every service is named in the contract from the
start, including still-empty ones, so a new service is covered the moment it has code rather than
whenever someone remembers to add it.

Boundaries are trivial to establish now and painful to retrofit once eight services import each
other. A rule a linter checks survives a late night; a rule in a document is a suggestion.

### Platform design

`docs/saas/` — twelve documents settling the shared/per-user split, container topology, storage
sizing, scheduler, tenant safety, onboarding, and operations. Start at
`docs/saas/PLATFORM_ARCHITECTURE.md`.
