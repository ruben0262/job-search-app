# SaaS platform docs — index

Design for turning the single-tenant job-search pipeline into a multi-user platform.

**Reference implementation:** `d:\Projects\job-search` — a working single-tenant instance. Its
`docs/PROJECT_STATE.md` is the spec for pipeline behaviour; these documents describe how that
behaviour becomes multi-tenant. Nothing here is green-field.

## Where the platform is — 2026-07-31

`CHANGELOG.md` is the history and its newest entries are the detail; this is the one-paragraph
version, and it lives here because the separate handover document was deleted on 2026-07-31 rather
than kept in sync with the changelog.

**Live:** `https://mahoraga-app.rubenlekli.com` on `169.58.16.241`, ten containers plus the
embedder stack, six systemd timers, 1,014 tests green. Two accounts. The whole web layer, auth,
onboarding, the setup chat, the jobs table and Telegram delivery are built and verified in a
browser.

**The one thing to know before touching scoring:** a full re-embed is half done. The corpus moved
from embedding `description` alone to `title_desc`, which invalidated every existing vector by
design — the mode is part of `EmbedderVersion.version`, so stale rows are refused rather than
silently mis-scored. Both users' active models were fitted on the old vectors. **Finish the
re-embed, then retrain, before judging anything downstream of the feed.**

**Recently closed:** the LinkedIn tier now runs (it never had, which was the real cause of "my feed
is quiet"), the enabled source list was replaced with the reference's own, and the trainer picks a
classifier by bake-off instead of using a fixed regression against a threshold that was never an
operating point.

---

## Read in this order

| Doc | What it settles |
|---|---|
| **[PLATFORM_ARCHITECTURE.md](PLATFORM_ARCHITECTURE.md)** | The shared/per-user boundary, container topology, storage sizing, database choice, open decisions. The design |
| [HARVESTER_PLAN.md](HARVESTER_PLAN.md) | **Live and current.** One shared scrape for all users: query-set dedup, fan-out budget, proxy ownership, embed-once — plus the per-user LinkedIn tier, built 2026-07-31. The busiest service on the box: three timers, cited by 17 modules |
| [SCHEDULER_PLAN.md](SCHEDULER_PLAN.md) | One queue, one lane per kind of work: fairness, restart safety, debounce. Written for four; there are seven now (`notify train docgen label process score setup`) — the design is unchanged, the list grew |
| [COLD_START_PLAN.md](COLD_START_PLAN.md) | **Implemented.** Signup → working feed. Kept because the labeller reads its thresholds live |
| [ONBOARDING_AND_PROCESSING.md](ONBOARDING_AND_PROCESSING.md) | **Implemented.** Which page a user is on is the server's answer, config as sections, and the dashboard as state rather than a wall of buttons |
| [REBUILD_PLAN.md](REBUILD_PLAN.md) | The setup chat that replaced one-shot profile capture, the two feed buttons, and the phase-5 cutover still outstanding |
| [TENANT_SAFETY.md](TENANT_SAFETY.md) | Isolation, secrets, plan-scoped quotas, bounded config schema, onboarding order, deletion |
| [TEST_STRATEGY.md](TEST_STRATEGY.md) | The ≥6/10 behaviour tests, anti-flake rules, write-order |
| [CODE_STRUCTURE.md](CODE_STRUCTURE.md) | Repo layout, module boundaries, the migration map from the reference instance, and how a one-service deploy stays safe |
| [AUTH_REUSE.md](AUTH_REUSE.md) | What to lift from the FlowSolutions Go auth system, and what to fix rather than copy |
| [PROFILE_CAPTURE_PLAN.md](PROFILE_CAPTURE_PLAN.md) | **§3–§4 reversed, §5–§7 built and load-bearing.** How a signup becomes a configured tenant. The Managed Agents sandbox it specifies was dropped (`libs/llm/client.py`: plain Messages, because Managed Agents is API-key-only and would have silently excluded every OAuth user), and the interview is now the setup chat of `REBUILD_PLAN.md`. §5's confirmation boundary and §6.3–6.4's rubric work still hold |
| [OPERATIONS_PLAN.md](OPERATIONS_PLAN.md) | **Half built, still relevant.** Health, alerts, email and backup are live; the admin console is read-only and the runbooks are unwritten |
| [GAPS_REVIEW.md](GAPS_REVIEW.md) | The 12-gap review, **re-audited 2026-07-31**. Two gaps it calls closed are implemented, tested and called by nothing |
| [ROADMAP.md](ROADMAP.md) | Phasing to first users, and what is deliberately deferred |

## Earlier research (still valid, partly superseded)

| Doc | Status |
|---|---|
| [MODEL_SELECTION_RESEARCH.md](MODEL_SELECTION_RESEARCH.md) | **Current.** Model/pricing ladder for scoring vs writing. Unchanged by the multi-tenant design |
| [`services/embedder/docs/PLAN.md`](../../services/embedder/docs/PLAN.md) | **Current and maintained.** The embedder's real design — measured numbers, the bge-small → bge-base reversal, and the corrections the VPS run forced. The service is built and deployed |
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
- ~~**Profile capture runs as a Managed Agents session**~~ — **reversed 2026-07-28.** Managed
  Agents authenticates by API key only, so the sandboxed design would have silently excluded every
  OAuth user, and it put a beta on the onboarding critical path. Capture is a plain Messages call
  and the interview is the setup chat (`REBUILD_PLAN.md`). Rendering stays deterministic code.
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

1. ~~**Managed Agents is beta** and sits on the onboarding critical path.~~ **Retired 2026-07-28.**
   The platform makes plain Messages calls; there is no beta on the onboarding path to pin. The
   OAuth-compatibility reason is in `libs/llm/client.py`.
2. **Profile-capture cost is unmeasured** — first thing a user spends, free plan, their own key.
   Measure before Phase 4.
3. **The rubric + feature-extractor workstream is the largest hidden task** and must ship as one
   piece (`PROFILE_CAPTURE_PLAN.md` §6.3–6.4).
4. **The `store.py` port is the critical path** — write test T2 against today's SQLite behaviour
   first.

## External answers — ✅ all cleared 2026-07-27

All three questions that needed a non-engineering answer have been reviewed and passed:

| # | Question | Status |
|---|---|---|
| 0 | **GDPR posture** — personal data reaching Anthropic (CVs, employment history), Telegram (job matches), and the proxy provider | ✅ cleared |
| 1 | **Scraping ToS / legal review** — the platform, not N individuals, becomes the responsible party | ✅ cleared |
| 2 | **Anthropic terms on subscription (OAuth) credentials in a hosted service** | ✅ cleared — the OAuth path may be offered |

⚠ **Clearing the review is not the same as building what it requires.** The GDPR posture depends on
artifacts that do not exist yet, and they are now scheduled into Phase 3 rather than assumed:

- a **privacy policy naming each processor** (Anthropic, Telegram, the proxy provider);
- **explicit consent at CV upload**, at the moment the file leaves for the Anthropic Files API;
- ~~**account deletion that also removes Anthropic-uploaded files**~~ — **moot since 2026-07-28.**
  Nothing is uploaded to the Files API any more: document text is sent inline in a Messages call
  and never stored outside Postgres, so the cascade *is* the whole story. A test asserts no row
  referencing the user survives in any table (`tests/test_gdpr.py`).

`internal/privacy` in FlowSolutions is the reusable pattern (`AUTH_REUSE.md` §5). See
`ROADMAP.md` Phase 3.

## Gap status

All blocking gaps are designed, including four found in the second review pass (G13–G16).
Four small ones stay open and belong inside their phase's
implementation rather than a document: G8 document lifecycle, G9 cost visibility, G11 account
lifecycle states, G12 model-artifact growth. See `GAPS_REVIEW.md`.
