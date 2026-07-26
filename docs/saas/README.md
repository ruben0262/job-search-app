# SaaS platform docs — index

Design for turning the single-tenant job-search pipeline into a multi-user platform.

**Reference implementation:** `d:\Projects\job-search` — a working single-tenant instance. Its
`docs/PROJECT_STATE.md` is the spec for pipeline behaviour; these documents describe how that
behaviour becomes multi-tenant. Nothing here is green-field.

## Read in this order

| Doc | What it settles |
|---|---|
| **[PLATFORM_ARCHITECTURE.md](PLATFORM_ARCHITECTURE.md)** | The shared/per-user boundary, container topology, storage sizing, database choice, open decisions. **Start here** |
| [HARVESTER_PLAN.md](HARVESTER_PLAN.md) | One shared scrape for all users: query-set dedup, fan-out budget, proxy ownership, embed-once |
| [SCHEDULER_PLAN.md](SCHEDULER_PLAN.md) | One queue, four lanes (notify / train / docgen / label): fairness, restart safety, debounce |
| [COLD_START_PLAN.md](COLD_START_PLAN.md) | Signup → working feed: three-stage onboarding, per-user rubric, backfill labelling, API vs OAuth |
| [TENANT_SAFETY.md](TENANT_SAFETY.md) | Isolation, secrets, plan-scoped quotas, bounded config schema, onboarding order, deletion |
| [TEST_STRATEGY.md](TEST_STRATEGY.md) | The ≥6/10 behaviour tests, anti-flake rules, write-order |
| [CODE_STRUCTURE.md](CODE_STRUCTURE.md) | Repo layout, module boundaries, the migration map from the reference instance, and how a one-service deploy stays safe |
| [AUTH_REUSE.md](AUTH_REUSE.md) | What to lift from the FlowSolutions Go auth system, and what to fix rather than copy |
| [PROFILE_CAPTURE_PLAN.md](PROFILE_CAPTURE_PLAN.md) | How a signup becomes a configured tenant: a sandboxed Managed Agents interview, a confirmation step, then deterministic rendering of the rubric, facts block, and base CV |
| [OPERATIONS_PLAN.md](OPERATIONS_PLAN.md) | Monitoring, alerts, user-facing error surfacing, action-capable admin console, transactional email, backup/restore, runbooks |
| [GAPS_REVIEW.md](GAPS_REVIEW.md) | The 12-gap review — all blocking gaps now designed |
| [ROADMAP.md](ROADMAP.md) | Phasing to first users, and what is deliberately deferred |

## Earlier research (still valid, partly superseded)

| Doc | Status |
|---|---|
| [EMBEDDER_SHARING_PLAN.md](EMBEDDER_SHARING_PLAN.md) | **Historical.** (The service moves to `services/embedder/` in Phase 1 — `CODE_STRUCTURE.md` §6.) Superseded by [`embedder/docs/PLAN.md`](../../embedder/docs/PLAN.md), which is the maintained plan with measured numbers and the decisions the VPS run reversed. The service is built and deployed in [`embedder/`](../../embedder/) |
| [MODEL_SELECTION_RESEARCH.md](MODEL_SELECTION_RESEARCH.md) | **Current.** Model/pricing ladder for scoring vs writing. Unchanged by the multi-tenant design |
| [CLAUDE_API_BACKEND_PLAN.md](CLAUDE_API_BACKEND_PLAN.md) | **Current** for the API-backend mechanics |
| [POOLED_CLAUDE_ACCOUNT_PLAN.md](POOLED_CLAUDE_ACCOUNT_PLAN.md) | **Deferred.** Assumes the platform resells Claude access via a prepaid wallet. Current design is bring-your-own-credential (decision D2). Revisit only if BYOK proves too much onboarding friction — the ledger design in §2 is good and worth keeping on the shelf |
| [SAAS_PLATFORM_PLAN.md](SAAS_PLATFORM_PLAN.md) | **Partly superseded.** §4's Model 1 (one container stack per user) is **rejected** — see `PLATFORM_ARCHITECTURE.md` §2 for the measured reasons. §7's open decisions are resolved. §5a (legal/ToS), §5f (operational burden), §5g (framework constraints) still stand |

## Decisions resolved 2026-07-26

- Shared harvest + shared corpus + shared embedder; per-user scores, models, state, credentials, and
  documents. Not one container stack per user.
- Telegram stays as the delivery channel; the web app handles signup, config, and monitoring.
- Frontend React + TypeScript; backend stays Python (the CPU-bound part is already offloaded to the
  Rust TEI embedder, so the speed argument for switching does not apply — `SAAS_PLATFORM_PLAN.md` §3).
- **D1 Postgres for everything** — one datastore, FK cascade, JSONB config.
- **D2 Both Claude credential types**, user chooses; API preselected, OAuth behind an explicit
  cost/quota warning and built second.
- **Profile capture runs as a Managed Agents session** — Anthropic hosts the sandbox; restricted
  toolset, read-only document mounts, deny-by-default egress, approval-gated writes. Rendering of
  the rubric and other artifacts stays deterministic code.
- **Onboarding order:** signup → verify email → free plan → credentials → capture → config →
  cold start. Capture spends the user's own credential, so an **API key is required to onboard**.
- **One plan (`free`) at launch**, but quotas are plan-scoped from day one so paid tiers are a row
  rather than a migration.
- **Admin console acts**, with a full audit trail and no impersonation.
- **Platform alerts reach users by email + in-app banner, never through the Telegram feed** — one
  of the failure modes *is* Telegram, and a channel cannot report its own death.
- **D3 (revised) Every user brings their own proxy, used strictly for their own fetches — never
  pooled.** The LinkedIn tier becomes per-user; every tier that needs no proxy stays shared. A user
  without a healthy credential simply does not see LinkedIn-sourced jobs, which removes free-riding
  and provider-ToS exposure by construction.

- **D4 (revised) Everything on the existing $20 Contabo VPS, self-managed Postgres** — so backups,
  PITR, and restore drills stay yours; needs the capacity check in `PLATFORM_ARCHITECTURE.md` §11.
- **D5 Invite-only at launch** · **D6 Parallel copies, decommission only when proven** ·
  **D7 Cloudflare Pages + `rubenlekli.com` subdomain, API via Tunnel** (no open inbound ports).
- **D8 EU data residency**, Postgres and backups both · **D9 Generated documents stay ephemeral**.

## Known risks carried knowingly

1. **Managed Agents is beta** and sits on the onboarding critical path. Pin the beta header; expect
   one contract change during the build. Manual entry is always the fallback.
2. **Profile-capture cost is unmeasured** — first thing a user spends, free plan, their own key.
   Measure before Phase 4.
3. **The rubric + feature-extractor workstream is the largest hidden task** and must ship as one
   piece (`PROFILE_CAPTURE_PLAN.md` §6.3–6.4).
4. **The `store.py` port is the critical path** — write test T2 against today's SQLite behaviour
   first.

## Needs an external answer (not engineering)

0. **GDPR posture** before invites go out. Personal data reaches third parties: **Anthropic** (CVs
   uploaded to the Files API during capture, employment history), **Telegram** (job matches), the
   proxy provider. Minimum: a privacy policy naming processors, explicit consent at upload, and
   account deletion that also removes uploaded files — today's deletion story covers Postgres only.
   `internal/privacy` in FlowSolutions is the reusable pattern (`AUTH_REUSE.md` §5).

1. **Scraping ToS / legal review** before signup opens beyond invites — with D5 this is a
   milestone, not a launch blocker (`SAAS_PLATFORM_PLAN.md` §5a).
2. **Anthropic terms on subscription (OAuth) credentials in a hosted service** — gates offering the
   OAuth path beyond the invited cohort.

## Gap status

All blocking gaps are designed, including four found in the second review pass (G13–G16).
Four small ones stay open and belong inside their phase's
implementation rather than a document: G8 document lifecycle, G9 cost visibility, G11 account
lifecycle states, G12 model-artifact growth. See `GAPS_REVIEW.md`.
