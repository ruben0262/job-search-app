# Test strategy — behaviour only, importance ≥6/10

**Status:** design, 2026-07-26. The bar set in the brief: test behaviour not implementation, no
brittle or flaky tests, and only tests that genuinely matter (6–7/10 importance and up).

**Reference:** `services/embedder/tests/` already works this way — `test_batch_invariance`, `test_fail_closed`,
`test_parity`, `test_priority_lane`, `test_version_assert`. Each asserts a *property a caller depends
on*, not an internal call sequence. Generalise that style; do not invent a new one.

---

## 1. What earns a test here

A test is worth writing if a silent failure of the thing it covers would be **expensive and hard to
notice**. Ranked by that, not by code coverage.

| ID | Test | Score | Why it matters |
|---|---|---|---|
| **T1** | **Tenant isolation** — seed two users with overlapping jobs; assert every read path (scores, seen, labels, docs, config, secrets, queue) returns only the caller's rows | **10** | The trade made in `PLATFORM_ARCHITECTURE.md` §2. A leak here is a company-ending bug, not a defect. **Release blocker** |
| **T2** | **Dedup semantics preserved** — url / canonical-URL / content-key layers behave identically to the reference instance across a fixture set including host variants, query-string noise, and same-title-different-city | **9** | "Never see the same job twice" is the core promise, and the port from SQLite → Postgres is exactly where it breaks. Write this **before** the port |
| **T3** | **Quota enforcement** — exceeding each cap returns an explicit error; nothing is silently truncated; the resource is not consumed | **8** | Silent truncation reads as "product is broken". Also the abuse ceiling |
| **T4** | **Secrets never egress** — API responses contain no secret value; a known secret string cannot appear in captured logs; DB ciphertext ≠ plaintext | **10** | Strangers' live credentials |
| **T5** | **Notification never double-sends** — deliver, kill the worker mid-job, let the claim expire, re-run: the job is sent exactly once | **8** | At-least-once queue + user-visible action. The classic duplicate-notification bug |
| **T6** | **Scheduler fairness + no starvation** — one user enqueues a burst of retrains; assert duplicate collapse to one pending job and that other users' jobs still progress | **7** | The trainer is concurrency 1; one user monopolising it stalls everyone |
| **T7** | **Notify lane re-arms on failure** — a delivery that raises still advances `next_run_at`; the user's feed resumes next cycle | **8** | The most likely *silent* platform failure: one user's feed dies forever and nobody notices |
| **T8** | **Embedding computed once** — embed the same job text for two users; assert the embedder backend was called once and both got identical vectors | **7** | The whole scaling argument (`HARVESTER_PLAN.md` §7). A cache regression is invisible until the CPU bill |
| **T9** | **Config validation** — out-of-range values rejected with a field-named error; a zero-match config is flagged before save | **7** | Primary onboarding failure mode |
| **T10** | **Cold start produces a usable feed** — new user + seeded corpus → Stage 0 feed non-empty immediately; after labelling + train, a personal model serves scores | **7** | The churn-critical path, end to end |
| **T11** | **Fail-closed on version mismatch** — scorer refuses to score when the embedder reports a different `(model, dim, version)` than the trained artifact | **8** | Already proven in `services/embedder/tests/test_version_assert.py`; extend to the per-user scorer. Scoring against the wrong vector space is silent garbage |
| **T12** | **Account deletion leaves no orphans** — delete a user; assert zero rows in every per-user table and that corpus rows are intact | **7** | GDPR + the "no user data in corpus" invariant |
| **T13** | **Proxy is never cross-used** — user A's credential is used only for A's fetches; a user with no healthy credential sees zero `source='linkedin'` rows in their feed | **8** | The whole basis of decision D3. A regression here silently reintroduces free-riding and provider-ToS exposure |

Below 6/10 and therefore **not** written: per-field serialisation round-trips, getter/setter coverage,
mock-call-count assertions, HTML rendering details, anything asserting a specific SQL string.

## 2. Anti-flake rules

These are the rules that keep the suite trustworthy. A flaky test is worse than no test — it trains
everyone to ignore red.

1. **No `sleep`.** Time is injected. The scheduler takes a clock; tests advance it. Claim expiry,
   debounce, and `next_run_at` are all testable deterministically, and any of them tested with real
   sleeps will flake on a loaded CI box.
2. **No real network.** Job sources, Telegram, and Claude are fixtures. One separate, explicitly-marked
   `@pytest.mark.live` suite may hit real endpoints, excluded from the default run — that is where
   "did the source's HTML change" belongs.
3. **No shared mutable state between tests.** Each test gets a fresh schema (transaction rollback or
   template database). Test order must never matter.
4. **Assert on observable outcomes**, never on how they were reached. "The vector was computed once"
   is asserted through a counting fake backend, not by asserting `embed()` was called with particular
   arguments.
5. **Deterministic seeds** for anything sampling (backfill pool selection).
6. **No assertions on wall-clock duration.** Performance belongs in a benchmark, not a unit test.

## 3. Layers

| Layer | Scope | Speed | Where the ≥6 tests live |
|---|---|---|---|
| Unit | Pure functions: dedup keys, config validation, query-set normalisation, quota arithmetic | ms | T2 (partly), T3, T9 |
| Service | One container + a real Postgres (testcontainers or a CI service), fakes at the edges | seconds | T1, T4, T5, T6, T7, T8, T11, T12 |
| End-to-end | Full compose, seeded corpus, fake Telegram + fake Claude | ~minutes | T10 |

Use a **real Postgres** at the service layer, never SQLite-as-a-stand-in. Half of what is being tested
(cascade deletes, `SKIP LOCKED` claim semantics, partial unique indexes) does not exist or behaves
differently in SQLite, so a substitute would test nothing that matters.

## 4. Ordering — what to write when

The point of this ordering is that the two tests protecting the riskiest work are written *before* the
work, not after.

1. **T2 first, before the Postgres port.** It is the regression harness for the port. Build it against
   the current SQLite implementation so it encodes proven behaviour, then keep it green through the
   migration.
2. **T1 as soon as the first per-user table exists.** Isolation must be true from the first row, not
   retrofitted.
3. **T4 with the secrets module**, same commit.
4. **T5/T6/T7 with the scheduler**, same commit — they define what "does not get stuck" means, and
   the definition should not be written afterwards from memory.
5. The rest as their subsystems land.

## 5. What is deliberately not tested

- Claude output quality. That is `cv_eval.py`'s job (deterministic + LLM-judge, already built and
  validated on 29 CVs) and it is a **measurement harness, not a pass/fail test** — LLM output is not
  deterministic enough to gate CI on.
- Live source availability. Sources break upstream constantly; that belongs in monitoring and the
  `live` suite, not in the blocking build.
- Frontend pixels. Test the schema contract the form is generated from (T9), not the rendering.
