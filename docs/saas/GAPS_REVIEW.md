# Gaps review — what the plan does not yet cover

**Status:** review, 2026-07-26. A pass over `PLATFORM_ARCHITECTURE.md`, `HARVESTER_PLAN.md`,
`SCHEDULER_PLAN.md`, `COLD_START_PLAN.md`, `TENANT_SAFETY.md`, `TEST_STRATEGY.md`, `ROADMAP.md`
looking for things that are missing rather than things that are wrong.

Ranked by "would this hurt, and how soon". Each gap says where it should be resolved.

---

## G1 — How the user's profile is captured ✅ **RESOLVED** → `PROFILE_CAPTURE_PLAN.md`

**Missing:** everything downstream depends on a structured profile of the user — their skills,
experience, target roles, constraints. Two separate systems consume it:

1. The **scoring rubric** (`COLD_START_PLAN.md` §3) — "WHO <user> IS" drives every label.
2. **Document generation grounding** — the 2026-07-15 A/B proved that without candidate facts in
   context, Haiku fabricates employers and metrics.

`SAAS_PLATFORM_PLAN.md` §2 raised this as a real decision — structured forms (a) vs a server-side
conversational interview (b) — recommended a hybrid, and **it was never resolved.** Every plan since
has quietly assumed the profile exists.

**Why it matters:** the current `/setup` flow is a Claude Code interview that reads the user's
documents and cross-references contradictions. A signup form cannot ask "these two documents disagree
on your job title, which is right?". But form-quality profiles produce weaker rubrics *and* weaker
CVs, and CV quality is the product.

**Recommendation:** hybrid, scoped explicitly.
- Structured form for the mechanical parts: target roles, locations, remote policy, seniority,
  languages, salary expectations, work eligibility. These map cleanly to fields and drive the rubric's
  hard gates.
- **CV upload + one server-side extraction call** for the narrative parts: experience entries, skills
  inventory, projects, metrics. Present the extracted result back as an editable form so the user
  corrects it. This preserves the document-reading value without running an unattended long-lived
  agentic session per signup.
- The edited result is the single source of truth for both the rubric and document grounding.

**Resolve in:** a new `PROFILE_CAPTURE_PLAN.md` before Phase 2 starts.

## G2 — Observability and admin tooling ✅ **RESOLVED** → `OPERATIONS_PLAN.md`

**Missing:** no design for monitoring. `SAAS_PLATFORM_PLAN.md` §5f flagged the operational burden;
nothing addressed it.

Once other people depend on this, "Ruben reads the logs" stops working. Needed at minimum:

- **Per-user health**: last successful harvest, last delivery, model version and age, queue position,
  quota state, proxy credential status.
- **Platform health**: queue depth per lane, dead-letter count, embedder queue depth, harvest budget
  consumption, Postgres size vs the §4 projection, proxy MB by user.
- **Alerts that matter**: any dead-lettered job; a user with no successful delivery in 24 h; embedder
  unverified; harvest budget exhausted; Postgres growth off-projection.
- **Admin read-only view** of a user's pipeline state for support, without impersonation.

The silent failures this catches are the expensive ones — a user's feed stopping is invisible to
everyone including the user, who assumes there are simply no jobs.

**Resolve in:** `OPERATIONS_PLAN.md`, before onboarding anyone outside the invited cohort.

## G3 — Transactional email ✅ **RESOLVED** → `OPERATIONS_PLAN.md` §6

**Missing:** email verification is required at signup (`TENANT_SAFETY.md` §5) but no sender is
designed. Also needed for: proxy credential failing, Claude credential invalid, delivery failures,
quota exhausted, password reset.

**Recommendation:** pick a provider (Postmark/SES/Resend) and reuse the approach in FlowSolutions'
`internal/outboundmail` (`AUTH_REUSE.md` §5). Non-trivial in practice — deliverability, SPF/DKIM,
bounce handling.

**Resolve in:** `OPERATIONS_PLAN.md` or its own short doc, Phase 3.

## G4 — Backup and restore ✅ **RESOLVED** → `OPERATIONS_PLAN.md` §7

**Missing:** `PLATFORM_ARCHITECTURE.md` §10.3 flags that Postgres is now a single point of failure
where the per-stack model had N independent ones. No backup design followed.

Two specifics that are easy to get catastrophically wrong:

1. **The KEK must be backed up separately from the database.** `TENANT_SAFETY.md` §2 requires the KEK
   to live outside the DB and its backups — but if it is not backed up *at all*, a restore yields a
   database full of undecryptable credentials. Both failure modes are silent until the day they matter.
2. **A restore drill is the only proof.** An untested backup is not a backup.

**Recommendation:** nightly `pg_dump` with off-box retention, KEK in a separate secret store with its
own backup, and a documented restore drill run once before launch and once per quarter.

**Resolve in:** `OPERATIONS_PLAN.md`, before Phase 4.

## G5 — Schema migrations 🟠

**Missing:** no migration tool chosen. The schema will change constantly through Phases 1–3, and the
corpus will be too large to recreate casually.

**Recommendation:** Alembic from the first table. Retrofitting migrations onto an existing database is
avoidable pain.

**Resolve in:** Phase 1, first commit.

## G6 — Telegram delivery failure handling 🟠

**Missing:** what happens when a user blocks their bot, deletes the chat, or revokes the token.
Telegram returns 403; the current design would retry, dead-letter, and go quiet.

**Why it matters:** the user's feed stops and neither side notices. This is the same silent-failure
class as G2 and it is the most likely one to actually occur.

**Recommendation:** on 403/chat-not-found, mark the user's Telegram state `broken`, stop enqueuing
deliveries, and **notify by email** with reconnect instructions. Surface the state in the dashboard.

**Resolve in:** `SCHEDULER_PLAN.md` §6, Phase 2.

## G7 — Timezones 🟠

**Missing:** the word does not appear anywhere in the plan, yet these all depend on it:

- "Notifications/day", "Claude spend/day", "proxy MB/day" — reset at whose midnight?
- Digest scheduling — a user wanting a 9 a.m. digest means *their* 9 a.m.
- Quota-reset messaging ("resets at 00:00 UTC" is currently hardcoded in `TENANT_SAFETY.md` §3).

Users are explicitly across Europe and internationally, so UTC-only is wrong from day one.

**Recommendation:** `users.timezone` (IANA), captured at signup with a browser-detected default. All
per-day counters reset at the user's local midnight. Store timestamps as `TIMESTAMPTZ` throughout —
never naive.

**Resolve in:** `TENANT_SAFETY.md` §3/§4 and the schema, Phase 1 (cheap now, painful later).

## G8 — Generated document lifecycle 🟡

**Partially covered.** The reference instance builds CVs into an ephemeral folder, delivers via
Telegram, discards, and keeps `applied.jsonl` as the durable record. Multi-tenant needs:

- Per-user storage cap on build artifacts, with the existing sweep generalised.
- A decision on whether users can re-download a past CV from the web dashboard (the reference instance
  cannot — documents are ephemeral by design). If yes, that is object storage and a retention policy,
  not a scratch folder.

**Recommendation:** keep ephemeral for v1, matching today's proven behaviour. Add the durable
per-user applied-log to Postgres (it is small and is the genuinely valuable record).

**Resolve in:** Phase 2.

## G9 — Per-user cost visibility 🟡

**Missing:** in API mode a user has a $2/day Claude cap, but nothing shows their spend. In OAuth mode
they are burning subscription quota with no visibility at all.

**Recommendation:** record token usage per call (already logged in the reference instance) into a
per-user usage table; show today/this-month on the dashboard. The ledger design in
`POOLED_CLAUDE_ACCOUNT_PLAN.md` §2 is over-engineered for BYOK, but its **integer-nanoUSD, never
floats** rule is worth taking.

**Resolve in:** Phase 3.

## G10 — Embedder is the second single point of failure ✅ **RESOLVED** → `OPERATIONS_PLAN.md` §8

**Noted but not planned for.** Fail-closed is the right behaviour and is already implemented — but if
the embedder is down, *nobody* gets scored. `PLATFORM_ARCHITECTURE.md` names Postgres as the SPOF and
does not name this one.

**Recommendation:** accept it for v1 (correct behaviour beats degraded scoring), but make it visible:
alert on `verified=false`, and let the harvest continue queuing unembedded jobs so recovery is a
drain rather than a gap. Confirm the queue-and-drain behaviour is actually what happens.

**Resolve in:** `OPERATIONS_PLAN.md`.

## G11 — Account lifecycle states 🟡

**Missing:** users are implicitly either active or deleted. Real states needed: `pending_verification`,
`active`, `suspended` (quota abuse), `dormant` (no login in N days), `deleted`.

**Why it matters:** dormant users consume harvest budget and, under D3, run their own LinkedIn tier
against their own proxy indefinitely. Pause the per-user loops for dormant accounts.

**Resolve in:** `TENANT_SAFETY.md`, Phase 3.

## G12 — Model artifact growth 🟢

Per-user `scorer.joblib` plus `.bak`. A few MB each, so trivial at 50 users — but unbounded if old
versions accumulate. Keep current + one previous, delete the rest.

**Resolve in:** Phase 2, one line in the trainer.

---

## Found in the second review pass (2026-07-26)

## G13 — `scorer/features.py` is a third personal artifact ✅ **RESOLVED** → `PROFILE_CAPTURE_PLAN.md` §6.3–6.4

**Missed in every earlier draft.** The plan named the rubric and the facts block as the personal
artifacts. The feature extractor is a third: `_CORE_TOKENS` is the operator's strengths list and
`offstack_count` counts hits against **his NOT-list**.

Worse than "one more thing to render": its docstring pins `FEATURE_NAMES` order because training and
serving must build the vector identically and old artifacts stay readable. So per-user token lists
mean **a profile edit changes what the feature columns mean, invalidating the trained artifact** — a
different invalidation chain from `profile_version`, which invalidates labels.

Tracked separately, the failure is silent: relabel and retrain both run, but the serving path holds a
model fit against the previous token lists and nothing errors. **Fix: one `profile_generation`
counter covering both, stamped on labels and artifacts, with the scorer failing closed on mismatch.**

## G14 — The migration spec covered only the corpus ✅ **RESOLVED** → `PLATFORM_ARCHITECTURE.md` §4

Earlier drafts designed `store.py` → Postgres and left ~10 other stores implicit. Two are easy to
underestimate:

- **`ats_etags.json` + `ats_etags.lock`** — the ETag store is what makes ATS polling near-free (~40s
  cold, near-free warm). It is guarded by a **file lock**, which does not survive multiple writers.
  Breaking it means re-downloading every board every cycle, silently.
- **`applied.jsonl`** — small, but it is the durable "what I applied to" record, and the one a user
  would be angriest to lose.

## G15 — The per-user LinkedIn tier had no global governor ✅ **RESOLVED** → `HARVESTER_PLAN.md` §5.2b

A regression introduced by the D3 revision: the fan-out budget governs the *shared* query set, and
moving LinkedIn to per-user loops left it behind. Per-user proxies give IP diversity but no throttle.
Fixed with a global concurrency ceiling, per-user jitter, and a kill switch.

## G16 — Embedding cache key unspecified 🟢 **RESOLVED** → `PLATFORM_ARCHITECTURE.md` §4

`refresh.py` mutates descriptions on live postings, so a `job_id`-keyed vector goes stale against text
it no longer represents. Key on `text_sha256`.

---

## Summary

| Gap | Status | Resolved in | Blocks |
|---|---|---|---|
| G1 profile capture | ✅ designed | `PROFILE_CAPTURE_PLAN.md` | Phase 2 |
| G2 observability | ✅ designed | `OPERATIONS_PLAN.md` | Phase 4 |
| G3 transactional email | ✅ designed | `OPERATIONS_PLAN.md` §6 | Phase 3 |
| G4 backup/restore | ✅ designed | `OPERATIONS_PLAN.md` §7 | Phase 4 |
| G10 embedder SPOF | ✅ designed | `OPERATIONS_PLAN.md` §8 | Phase 4 |
| G5 migrations | 📌 folded into plan | `ROADMAP.md` Phase 0/1 | Phase 1 |
| G7 timezones | 📌 folded into plan | `ROADMAP.md` Phase 0/1 | Phase 1 |
| G6 Telegram failure | 📌 folded into plan | `SCHEDULER_PLAN.md` §6 + `OPERATIONS_PLAN.md` §4 | Phase 2 |
| G8 document lifecycle | ✅ **closed by D9** | Ephemeral, as today | — |
| G9 cost visibility | 🟡 open | — | Phase 3 |
| G11 lifecycle states | 🟡 open | — | Phase 3 |
| G12 artifact growth | 🟢 open | — | Phase 2 |
| **G13 features.py is personal** | ✅ designed | `PROFILE_CAPTURE_PLAN.md` §6.3–6.4 | Phase 2 |
| **G14 non-corpus stores** | ✅ designed | `PLATFORM_ARCHITECTURE.md` §4 | Phase 1 |
| **G15 LinkedIn governor** | ✅ designed | `HARVESTER_PLAN.md` §5.2b | Phase 1 |
| **G16 embedding cache key** | ✅ designed | `PLATFORM_ARCHITECTURE.md` §4 | Phase 1 |

**All blocking gaps are now designed.** The four still open (G8, G9, G11, G12) are small, well
understood, and belong inside their phase's implementation rather than in a document of their own.

## Implementation status — audited 2026-07-27

Designed and *built* are different things. This tracks the second, for the gaps Phase 1 owns.

| Gap | Built? | Evidence |
|---|---|---|
| **G5 migrations** | ✅ **closed** | Alembic from the first table, 4 revisions, no hand-applied DDL. CI proves `upgrade head` from empty and fails on model/migration drift |
| **G7 timezones** | ✅ **closed** | `users.timezone` (IANA) + `TIMESTAMPTZ` everywhere, asserted against the live schema rather than the models |
| **G14 non-corpus stores** | ✅ **closed** | `ats_tokens`, `ats_etags`, applied, labels, feedback all ported. The `ats_etags` file lock is gone — replaced by row-level upsert, with an interleaved-write test |
| **G15 LinkedIn governor** | 🟡 **policy + fetch layer built, loop deferred** | Concurrency ceiling, jitter and kill switch in `services/harvester/governor.py`; the visibility gate enforced; and `services/harvester/fetch.py` now makes D3 structural — one client factory, per-user leases, LinkedIn-only routing, `trust_env=False`, metered bytes, quarantine on consecutive failures. The loop that calls it is an ordinary queue job and waits on `libs/queue` (Phase 2) |
| **G16 embedding cache key** | ✅ **closed** | Vectors key on `text_sha256`, and `set_embedding()` refuses a vector whose digest no longer matches — the race where bulk embedding outlives its text. Routing harvest through `/embed/bulk` is step 5 |

Two findings from building them, both recorded in `CHANGELOG.md`:

1. **A dedup layer was missing from the first port.** The reference matches on the resolved *Apply
   URL* (`processed.app_canonical`) as well as the job URL. Without it, a LinkedIn posting and the
   Greenhouse row its Apply button resolves to are two rows in every feed. Added as a fourth layer,
   checked in both directions.
2. **The per-user keyword cap was written twice** — a literal in the config schema and a constant in
   the harvester — which is how limits drift apart. Moved into `libs/contracts`, the only package
   both may import from, since `libs` must never import a service.
