# SaaS Platform Plan — Web Signup Frontend for Self-Service Onboarding

**Status:** research/constraints only, no build started. Origin: 2026-07-24 conversation (see
`andras-job-search/docs/CHANGELOG.md` same date) about optimizing the shared-VPS scoring service,
which led into the bigger idea below.

**Read first:** `PRODUCTIZATION.md` (de-personalization checklist for a forkable copy) and
`FRIENDS_ROLLOUT.md` (the current real deployment model — Option A: friend's own machine,
Option B: multi-tenant on Ruben's VPS, one Docker Compose project per friend). This plan is the
next evolution of that: instead of a friend manually running `/setup` inside Claude Code, cloning
the repo, and hand-editing `.env`, a **web app** does the onboarding, provisioning, and ongoing
management — while the actual job search still runs the same way underneath (Docker containers,
Telegram bot for day-to-day use, the trained scorer).

## 1. The vision, as stated

- Non-technical users **sign up on a website**, fill in their profile/preferences through a form
  (replacing the current Claude-Code-CLI `/setup` interview), and the platform **provisions and
  runs their own instance** for them.
- Execution/day-to-day interaction **stays on Telegram** (build CV, get notified, `/rate`, etc.) —
  the web app is for setup, monitoring, and account management, not a replacement for the bot.
- The web app should let users **train their model**, **view stats**, and needs **a way to sync
  their data** (see open question in §7 — this needs a decision on what "sync" means here).
- **Frontend: React + TypeScript.** Decided, not open.
- **Backend: open, and speed is the top priority** — Python is not assumed to be the right choice.
- Before building: **research constraints deeply** and record them (this document).

## 2. The core architectural shift this actually requires

This is the part worth being explicit about, because it's a bigger change than "add a web
frontend to the existing thing":

**Today**, onboarding is a *conversation*: `/setup` runs inside an interactive Claude Code
session, reads the user's documents, asks follow-up questions in natural language, and writes
`CLAUDE.md` + the skill files + `pipeline/config.yaml` + `.env` directly onto disk in that user's
own repo checkout. The quality of the resulting profile depends on that back-and-forth interview
(cross-referencing documents, asking about ambiguous cases, negotiating target roles) — it is not
a static form.

**Moving to a web signup form** means one of two things, and this is a real decision, not a
detail:

- **(a) Re-implement the interview as structured forms** — lose some of the conversational
  nuance (a form can't naturally ask "these two documents disagree on your job title, which is
  correct?" the way `/setup`'s Path A cross-reference step does), but gets a fully self-service,
  no-CLI-needed flow. Would still call the Claude API (not the interactive CLI) server-side to
  do the actual CV-writing/profile-synthesis work once form data is submitted.
- **(b) Keep a conversational step, run server-side** — the web app triggers a real Claude
  Code-style agentic session (via the API, not the CLI, since this needs to run unattended for a
  paying user, not in a developer's terminal) that does the same document-reading/interview
  process, just orchestrated by the backend instead of a human running `/setup` themselves. This
  preserves quality but is a meaningfully bigger engineering lift (running and monitoring
  long-lived agentic sessions per signup, handling failures/retries, cost per session).

**Recommendation:** start with (a) for the initial roles/targets/preferences (structured, this
maps cleanly to a form), but keep (b)'s document-upload + cross-reference step for CV building —
that part's value comes specifically from reading messy source documents, which a form can't
replace. This is a hybrid, and should be scoped explicitly before building, not discovered
mid-build.

## 3. Backend language choice — honest analysis, not a default

"Speed is the most important thing" needs unpacking, because different parts of this system have
completely different performance profiles:

| Part of the system | Bound by | Does backend language choice matter? |
|---|---|---|
| Web app requests (signup, dashboard, config forms) | Low request volume at realistic early user counts (tens-hundreds) | **No** — any modern backend (Node, Python, Go) is more than fast enough here. This is CRUD-shaped, not compute-shaped. |
| Background job orchestration (per-user scrape/score cycles, spinning up Docker per tenant) | External I/O latency (network scraping, Claude API round-trips, Docker daemon calls) | **Marginal** — the wait is on the network/Docker, not the orchestrator's own CPU. Concurrency model matters more than raw language speed (can the orchestrator handle many simultaneous long-running background jobs without blocking). |
| ML scoring (embedding + classifier) | Genuinely CPU-bound | **Solved** — `services/embedder/docs/PLAN.md`. Centralized into a purpose-built service (HuggingFace TEI, Rust), built and deployed, regardless of what the main backend is written in. This is the ONE part of the system where "Python is slow" is actually true and actually matters, and it's already being architected around. |

**Honest conclusion:** the case for abandoning Python specifically *for speed* is weak once the
CPU-bound part (embedding) is already being offloaded to a dedicated Rust-based service. The
scraping/orchestration layer is I/O-bound, not CPU-bound — it's currently Python (`httpx`,
threading) and works fine at the current scale; multi-tenant orchestration adds more *concurrent
work items*, not more *CPU-intensive* work per item.

That said, there's a **different, legitimate reason** to consider a different backend, less about
raw speed and more about fit for this specific job:

- **Node.js/TypeScript** — same language as the React frontend (already decided). Shared types
  between frontend and backend, one hiring/skill pool, huge ecosystem for auth/billing (Stripe,
  Clerk/Auth.js), and genuinely fine performance for an I/O-bound orchestration + CRUD backend.
  Downside: the existing scraping/scoring/CV-building Python code (`pipeline/`, `scorer/`) would
  either need a rewrite or the Node backend becomes a thin orchestration layer that still shells
  out to / calls the existing Python containers (very workable — the containers already
  communicate over HTTP, e.g. `scorer-service`).
- **Go** — excellent fit specifically for the *provisioning/orchestration* problem: first-class
  Docker SDK support (this system's core operational need — spinning up/tearing down per-tenant
  containers programmatically), strong concurrency primitives for managing many simultaneous
  background jobs, single static binary deployment, low memory footprint (relevant on a
  resource-constrained VPS — see the whole prior conversation about memory pressure). Very common
  and well-proven pairing: Go backend + React/TypeScript frontend, talking over a typed REST or
  gRPC/OpenAPI-generated client.
- **Keep Python for the backend, add TypeScript only for the frontend** — lowest-risk, reuses
  100% of the existing `pipeline/`/`scorer/` code untouched, backend just gains a thin API layer
  (FastAPI, which the codebase already uses for `scorer/service.py`) in front of what exists. Not
  "wrong" for speed reasons (see table above) — the actual argument against this option is team
  preference / wanting a unified stack, not performance.

**This document does not pick one** — it's a real trade-off between "reuse everything, lowest
risk" (Python) and "best tool for the per-tenant orchestration problem specifically" (Go) and
"one language everywhere" (Node). Recommend deciding this explicitly, informed by the table above,
rather than defaulting to "Python is slow" as the reason to switch — that reason doesn't actually
hold for the parts of this system that would run on the web backend.

## 4. Multi-tenancy / isolation model — the biggest architectural decision

Two fundamentally different models, with very different risk profiles:

> **REJECTED 2026-07-26** — see `PLATFORM_ARCHITECTURE.md` §2. Measured grounds: ~1.9GB scorer
> RSS per stack, N x proxy GB for identical pages, 44GB vs 0.87GB storage at 50 users, and no
> cold-start corpus. The adopted design is a shared harvest core with a per-user tail.

### Model 1 — Automate what Option B already does (~~RECOMMENDED for v1~~ REJECTED)
Keep the current architecture: **one isolated Docker Compose project per user** (exactly
`FRIENDS_ROLLOUT.md`'s Option B), but have the web app's backend provision it automatically
instead of a human running `docker compose -p <user> up -d` by hand. Each user's containers,
`.env`, trained model, and data stay fully separated at the OS/container level — the strongest,
simplest form of isolation, and it's already proven (this is literally how `andras-job-search`
runs today, alongside `job-search`, on the same VPS).

- **Pros:** reuses the entire existing codebase unchanged; isolation is "free" (separate
  containers, separate filesystems, separate `.env` files — a bug in one tenant's request can't
  leak into another's data because they're different processes entirely); matches a pattern
  that's already been operated in practice.
- **Cons:** doesn't scale to hundreds/thousands of users on one VPS (each tenant needs their own
  `scorer-service` + `watcher` + `notifier` — RAM/CPU multiply per tenant, exactly the shared-VPS
  pressure problem from this whole conversation, just at bigger scale). The embedder-sharing plan
  (`services/embedder/docs/PLAN.md`) becomes load-bearing here, not optional — without it, N tenants
  means N copies of the embedding model in RAM, which won't scale past a handful of users on any
  single box.

### Model 2 — True shared multi-tenant service (one process, many users' data)
A single running backend serves all users; user data is partitioned by ID inside shared
databases/storage rather than separate containers. This is how most real SaaS products work at
scale (much cheaper per-user, since you're not running N full copies of every service).

- **Pros:** genuinely scales; much lower marginal cost per additional user.
- **Cons:** a MUCH bigger rewrite (the current code assumes one profile, one `.env`, one set of
  JSON data files per checkout — none of it is multi-tenant-aware); isolation now depends
  entirely on application-level correctness (a query missing a `WHERE user_id = ?` becomes a data
  leak between customers, not just a bug) — a materially higher security bar; and the
  scraping/scoring pipeline would need real multi-tenant scheduling (whose jobs run when, fair
  resource sharing) rather than "just run the container."

**Recommendation:** build Model 1 first (automate Option B), pair it with the embedder-sharing
service so it doesn't blow up VPS memory as tenant count grows, and treat Model 2 as a much later
migration if/when user count genuinely demands it. This is the same "prove it small, then scale"
sequencing already used elsewhere in this project's docs (see the embedder plan's own rollout
order).

## 5. Constraints — the deep list requested

### 5a. Legal / ToS / scraping-risk (the sharpest new risk vs. today)
- Today's deployments are 1-4 trusted individuals scraping for their own personal job search.
  LinkedIn/Indeed's terms of service already sit in a grey area for automated scraping generally;
  a **public, self-serve signup product** materially raises visibility and aggregate scraping
  volume, which raises the realistic likelihood of attention/blocking versus a handful of
  friends. This is a genuine risk to evaluate before any public launch, not just a technical
  constraint — get real legal advice if this moves toward being a paid public product, not
  engineering judgment alone.
- The residential proxy model changes too: today Ruben shares his own proxy URL with 2-4 trusted
  friends and eats the (metered) bandwidth cost personally. That does not work for a public
  product — either each user brings their own proxy (friction, but no shared cost/liability), or
  the platform provisions/bills for proxy usage per user (real, scaling cost — DataImpulse-style
  providers charge ~$1/GB, and scraping cost scales with user count and their search breadth).

### 5b. Cost model / billing
- Real, measured costs exist already (`docs/PROJECT_STATE.md`): a single CV build costs roughly
  **$0.11-0.14** in Claude usage; each user also needs their own Claude subscription/API access,
  a VPS-equivalent allocation of compute, and (per 5a) proxy bandwidth. A SaaS product needs an
  explicit answer to: does the user bring their own Claude subscription (today's model, per
  `FRIENDS_ROLLOUT.md` — "no cost to Ruben, in either option"), or does the platform provide
  Claude access and charge for it (materially different cost/billing architecture, usage
  metering, abuse limits)?
- Compute cost per tenant under Model 1 (§4): each additional user needs their own
  `scorer-service` + `watcher` + `notifier` running somewhere. This is the same RAM math this
  whole conversation has been about, just multiplied by tenant count — needs a real per-user cost
  estimate before pricing anything.

### 5c. Secrets management
- Today: each user's Claude token, Telegram bot token, and (shared) proxy credentials live in a
  plaintext `.env` file, gitignored, on whichever machine runs Docker. That is acceptable for a
  handful of trusted friends running their own instance (Option A) or trusting Ruben with root on
  his own VPS (Option B) — it is **not** an acceptable model for a public product holding
  strangers' credentials. Needs real secrets management (encrypted at rest, scoped access, likely
  a secrets manager rather than files) before any public signup.

### 5d. Data sync — needs a decision, not just a constraint
The request mentions "a way for users to sync their data" without specifying sync between what
and what. Candidate meanings, each with a different design:
- **Local-first, no hosting** (today's Option A): nothing to "sync" — everything lives on the
  user's own machine. Doesn't fit "run their own bot from the interface" framing though.
- **Fully hosted** (Model 1 or 2 above): there is no separate "local" copy to sync WITH — the web
  app IS the source of truth, no sync needed, just normal read/write to the hosted instance.
- **Hybrid** (some users self-host, some are hosted, and they want to move between the two, or
  keep a local backup of their hosted data): THIS needs an explicit export/import or sync API —
  worth clarifying which of these three is actually meant before designing anything here, since
  each implies a different, non-overlapping feature.

### 5e. Security / isolation bar
Directly tied to §4's model choice. Model 1 (separate containers) gets strong isolation close to
free. Model 2 (shared service) requires the application itself to be correct about tenant
isolation on every single query/file access — a much higher and more failure-prone bar,
especially early on. This alone is a strong argument for Model 1 first.

### 5f. Operational/support burden
Running other people's infrastructure (even in separate containers) means Ruben (or whoever runs
the platform) now owns uptime, monitoring, and incident response for other people's job searches
— a step up from "my own tool, plus I help 2-4 friends informally." Needs at least a plan for:
monitoring per-tenant container health, alerting on failures, and a support channel for issues
(distinct from Ruben personally reading logs, which is the current de facto model per this whole
conversation).

### 5g. Existing framework constraints that still apply unchanged
From `FRIENDS_ROLLOUT.md`, still true at any scale: numpy version split between the pipeline
(1.26) and scorer (2.x) requires separate Docker images (already solved, keep this pattern);
per-user Telegram bot (via BotFather) is already a clean multi-tenant-friendly pattern — no
change needed there regardless of which model above is chosen.

## 6. Recommended phased approach

1. ~~**Ship the embedder-sharing service first**~~ **Done** (`services/embedder/docs/PLAN.md`) — it was a prerequisite
   for Model 1 scaling past a couple of tenants, and it's already scoped and lower-risk than
   anything in this document.
2. **Decide the backend language** using §3's honest trade-off table, not a "Python is slow"
   assumption — the CPU-bound part is already being handled separately.
3. **Build Model 1 (automate Option B)** — a web app that provisions one isolated Docker Compose
   project per signup, replacing the manual `/setup` + hand-edited `.env` flow with forms +
   server-side Claude API calls. Reuses the existing `pipeline/`/`scorer/` code entirely
   unchanged.
4. **Resolve the data-sync question (§5d)** concretely — pick one of the three meanings before
   designing any sync feature.
5. **Get real legal input on §5a** before any public (non-friends) launch.
6. **Only after Model 1 is proven and user count genuinely demands it**: evaluate migrating
   toward Model 2's true multi-tenant architecture.

## 7. Open decisions for the user

- Backend language: Python (lowest risk, reuse everything) vs Go (best fit for per-tenant
  container orchestration) vs Node/TypeScript (unified stack with the frontend). See §3.
- Which "data sync" meaning is actually intended (§5d).
- Who bears the Claude subscription/API cost per user, and the proxy cost per user (§5b) — this
  is really a business-model decision, not just an engineering one.
- Public product vs. still a friends-only rollout at larger scale — changes the legal risk
  calculus in §5a substantially.
