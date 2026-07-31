# Claude API Backend — Design Plan

## Implementation status — audited 2026-07-31

**Read this doc as reference-instance history plus one still-live conclusion.** It was written for
`d:\Projects\job-search` — `pipeline/lib/claudecli.py`, `pipeline/config.yaml`, `scorer/label.py` —
and this platform never had that shape.

**§3's premise does not apply here.** There is no `claude -p` subprocess backend to switch away
from: `libs/llm/client.py` has made plain Messages calls since the first line of it, on the
**user's own credential** (D2, BYOK). So `claudecli.py` / `claudeapi.py` / `claudeback.py`, the
`CLAUDE_BACKEND` flag and the per-workload override are all moot — there is one backend and
nothing to dispatch between. Also note the platform reversed the *other* direction from what §1
assumes: Managed Agents was dropped precisely because it is API-key-only, which would have
excluded every OAuth user.

**§8b is the part that carried, and it is load-bearing.** Its verdict — batch discount, prompt
cache and rate limits are scoped per organisation, so cross-user pooling under BYOK saves nothing
— is why the platform pools the *embedder* (a deterministic artifact) and not Claude calls, and it
is the reasoning behind `POOLED_CLAUDE_ACCOUNT_PLAN.md` staying deferred. That conclusion needs no
change.

**Two real savings named here are unclaimed, and both still apply per user:**

| Lever | State |
|---|---|
| **Batch API (-50%)** | **Not implemented.** `services/labeller` chunks 50 jobs into one *synchronous* Haiku call (`label.py::BATCH_SIZE`) — that is request-level chunking, not `messages.batches`. Cold-start labelling is exactly the offline, latency-insensitive workload §5 says should use it |
| **Prompt caching** | **Not implemented.** No `cache_control` anywhere in the tree. §5's caveat still governs whether it is worth anything: measure the rubric prefix against Haiku's 4096-token floor first, and verify `cache_read_input_tokens > 0` rather than assuming |
| **Budget cap / ledger** | **Not implemented.** No client-side spend ledger. Less pressing than in the reference, because under BYOK the spend is the user's own and a Console limit is theirs to set — but there is no cost visibility in the product either, which `GAPS_REVIEW.md` G9 already tracks |

§7's rollout gates (score A/B before flipping, measure the real multiple rather than assuming
"120x") remain the right way to do it if the batch path is ever built.

---

**Original status:** planning only, no code yet. This is an experiment proposal: add a **direct
Anthropic API** backend alongside the OAuth (`claude -p`) backend the pipeline uses today, with a
hard budget cap and a one-flag switch between the two. Keep OAuth fully working; make API opt-in.

**Change-control note:** the document-generation prompts and the scoring rubric are SACRED (see
`CLAUDE.md` → Prompt & Rule Change Control). This plan does **not** touch any prompt. It changes only
*how the same prompt is delivered to Claude* (subprocess CLI vs HTTPS API). Any real switch-over is
gated on an A/B (`pipeline/cv_eval.py` for documents, relabel-compare for scores) proving output
quality is unchanged.

---

## 1. Where we are today (OAuth / `claude -p`)

Every Claude call in the pipeline goes through `pipeline/lib/claudecli.py` → the `claude` Claude-Code
CLI in headless mode (`claude -p --output-format json`). Key facts:

- **Billing:** draws from the normal **Pro/Max subscription pool** — no marginal dollar cost, but it
  consumes the *interactive* quota and is bounded by opaque plan rate limits, not a dollar figure
  (see the billing note at the top of `claudecli.py`). `total_cost_usd` in the envelope is a
  *subscription-equivalent estimate*, not an amount actually charged.
- **Two distinct workloads** ride this one path:
  1. **Mechanical scoring / labeling** — `scorer/label.py`, `pipeline/prompts/score.md`,
     `scorer/prompts/label.md`. Self-contained rubric, run `bare=True` (outside the repo so no
     `CLAUDE.md`/skills load), `allowed_tools=""`, `max_thinking_tokens=0`, `max_turns=2`. One shot,
     no tools, no thinking. **This is the cheap, batchable, cacheable workload.**
  2. **Document generation** — CV / cover / outreach / interview (`pipeline/cv_builder.py`,
     `processor.build_cvs`). Deliberately **loads `CLAUDE.md` context** (grounds the writing — a
     2026-07-15 A/B showed bare/no-thinking caused fabricated employers/metrics) and uses tools
     (`WebFetch,WebSearch,Read,Write,Edit,Bash`). `single_shot` = one text call; `agentic` = ~100-turn
     agent. **This workload needs the CLI's tool loop + project-context loading.**
- **Model config** lives in `pipeline/config.yaml`: `model` (scoring/health), `cv_model`,
  `scoring.shadow_model`, `scoring.verify_model` — all currently `claude-haiku-4-5-20251001`.

**The pain points this experiment targets:**
- Scoring/labeling **burns interactive Pro/Max quota** — the same pool the on-demand CV builds and
  any manual Claude Code use draw from. A bulk relabel of a few thousand rows can starve it.
- Cost is **not a dollar figure you can cap** — you get rate-limit errors, not a budget.
- No **batch discount** and no **explicit prompt caching** — the CLI path can't opt into either.

---

## 2. The idea, evaluated honestly

> User's framing: "use the Claude API with a budget for a much cheaper model than Haiku that would
> perform ~120x better for the task."

Two corrections up front, so the plan rests on real numbers:

1. **There is no model cheaper than Haiku** in the current line-up. Haiku 4.5 is the floor at
   **$1 / $5 per MTok** (in/out). So "a cheaper model" is not where the savings come from.
2. **"120x" is unmeasured** and almost certainly not literal. The genuine wins are below; the real
   multiple is a thing to *measure* (§7), not assume.

Where the direct API **actually** helps the mechanical scoring workload:

| Lever | Mechanism | Realistic saving |
|---|---|---|
| **Batch API** | Scoring/labeling is not latency-sensitive. `POST /v1/messages/batches` runs the same requests at **50% off**, results within ~1h (max 24h). | **-50%** on every scored token. |
| **Prompt caching** | The rubric + system prompt are **byte-identical** on every batch; only the jobs JSON varies. Cache the stable prefix → repeated prefix bills at ~0.1x. | Up to **~90%** off the *cached prefix* — see the Haiku 4096-token minimum caveat in §5. |
| **Quota separation** | API spend is a **separate dollar-metered pool** from Pro/Max. Bulk relabels stop starving interactive quota. | Not a token saving — an **operational** win (no more quota contention). |
| **Hard budget cap** | A client-side ledger + Anthropic Console workspace spend limit gives a real **dollar ceiling**, instead of hitting opaque rate limits. | Cost *control*, not cost *reduction*. |
| **No CLI/envelope overhead** | Direct `messages.create` skips the Claude-Code system prompt, tool-doc preamble, and JSON envelope. `bare=True` already strips most of this, so the delta is small. | Marginal. |

**Net:** for the scoring/labeling path, batch (0.5x) × cache (partial) plus quota separation is a
strong, real case. It is *not* 120x, but a **2x–5x** effective token-cost reduction plus removing
quota contention is very plausible and worth the build.

For **document generation**, the API case is weaker: it needs the tool loop and `CLAUDE.md` context
that the CLI provides for free. Re-implementing that over the raw API (inject `CLAUDE.md` as a system
prompt + hand-roll a `WebFetch/WebSearch/Write` tool loop) is real work and risks regressing the
SACRED output quality. **Recommendation: keep document generation on OAuth for now**; only the
scoring/labeling path moves to the API backend in phase 1.

---

## 3. Design: a backend abstraction + one switch

Introduce a thin backend seam so callers don't care which transport runs the prompt.

```
pipeline/lib/
  claudecli.py     # existing — OAuth `claude -p` subprocess  (unchanged)
  claudeapi.py     # NEW — direct Anthropic API via the `anthropic` SDK
  claudeback.py    # NEW — dispatcher: picks cli|api from config, returns a ClaudeResult
```

- `claudeapi.run_prompt(...)` mirrors `claudecli.run_prompt(...)` **exactly** — same signature, same
  `ClaudeResult` dataclass (already in `claudecli.py`), so nothing downstream changes. It maps:
  - `prompt` → a single `user` message.
  - `system_prompt` → the `system` field (defaults sensibly when `None`).
  - `bare=True` → no-op (the API never loads `CLAUDE.md`; it's always "bare"). This is *why* the
    scoring path is the natural first mover.
  - `max_thinking_tokens=0` → omit thinking (Haiku 4.5 uses `budget_tokens`; 0 = off).
  - `allowed_tools` → ignored for the scoring path (it's already `""`).
  - `expect="json"` → reuse `claudecli._extract_json_array` on the response text (share the helper).
  - `cost_usd` → compute from `response.usage` × the model's per-MTok rates (Haiku $1/$5), since the
    API returns tokens, not a dollar figure.
- **Model ID:** the SDK wants the alias `claude-haiku-4-5` (no date suffix). Keep the config's
  `claude-haiku-4-5-20251001` for the CLI; the API backend strips the date suffix when it sees one.

### The switch

A single env/config knob, defaulting to the current behaviour:

```yaml
# pipeline/config.yaml
claude_backend: ${CLAUDE_BACKEND:-cli}     # cli | api   (cli = today's OAuth path)
```

`claudeback.run_prompt(...)` reads it and dispatches. **Per-workload override** so we can move only
scoring first:

```yaml
scoring:
  backend: ${SCORING_BACKEND:-}            # blank = inherit claude_backend; api|cli to force
```

Document generation ignores the switch in phase 1 (always `cli`) until §6 is built and A/B-passed.

---

## 4. Budget cap

Two layers, belt and suspenders:

1. **Anthropic Console workspace spend limit** — set a hard monthly USD cap on the API key's
   workspace. This is the real backstop; the key simply stops working past it. Documented as a manual
   setup step, not code.
2. **Client-side ledger** — `data/api_budget.json` tracks cumulative spend for the current period.
   Before each API call, `claudeapi` checks `spend + est_cost <= CLAUDE_API_BUDGET_USD`; if it would
   exceed, it **refuses the call, logs, and Telegram-alerts** (reuse `pipeline/notifier.py`). After
   each call it adds the real `usage`-derived cost. Config:

```yaml
api:
  budget_usd: ${CLAUDE_API_BUDGET_USD:-10}   # per-period soft cap enforced client-side
  budget_period: ${CLAUDE_API_BUDGET_PERIOD:-month}   # month | week | all-time
```

The ledger is advisory (racy under concurrency, and estimates before the real usage lands) — the
Console limit is the authority. That's fine: the ledger's job is a friendly early warning and a
visible number in Telegram, not airtight accounting.

---

## 5. Batch API + caching (the actual saving)

- **Batch path:** labeling already scores in chunks of 50 (`scorer/label.py::_score_all`). The API
  backend can submit all chunks as one `messages.batches.create`, poll to `ended`, and collect
  results keyed by `custom_id` (= chunk index or job URL). Latency goes from seconds to up to ~1h,
  which is **fine for the nightly/bulk labeling** — but **not** for the on-demand `verify_high`
  cascade or the watcher, which need a synchronous answer. So:
  - `label`/`relabel`/`refetch-thin` (bulk, offline) → **batch API** (`-50%`).
  - `verify_high`, watcher, health check → **synchronous** `messages.create` (full price, low volume).
- **Caching caveat (must verify):** Haiku's **minimum cacheable prefix is 4096 tokens**. Put
  `cache_control` on the last stable block (rubric + system). If the rubric prefix is *below* 4096
  tokens it **silently won't cache** (`cache_creation_input_tokens: 0`). Action item in §7: measure
  the rubric token count with `count_tokens`; if under 4096, caching buys nothing and only batch
  applies. Do not assume the cache hit — verify `usage.cache_read_input_tokens > 0`.

---

## 6. Phase 2 (optional, later): document generation over the API

Only if phase 1 proves out and there's a reason to move documents too. Requires re-creating what the
CLI gives for free:

- Inject `CLAUDE.md` (the profile context) as the `system` prompt — it is load-bearing for factual
  grounding (2026-07-15 A/B).
- Hand-roll the tool loop for `WebFetch`/`WebSearch`/`Write` (the SDK tool-runner + server-side
  `web_search`/`web_fetch` tools cover search/fetch; file writes are client-side).
- Re-run `pipeline/cv_eval.py` A/B (API vs CLI, same prompt) and **only** keep it if scores are
  unchanged. High risk of regressing SACRED output; not worth it unless quota contention on the CV
  path becomes a real problem.

**Default recommendation: do not do phase 2** unless there's a concrete driver.

---

## 7. Validation & rollout

Nothing switches by default. Rollout is measured:

1. **Prereq:** add `ANTHROPIC_API_KEY` to `.env`; set a Console workspace spend limit; `pip install
   anthropic` in the pipeline container.
2. **Token/caching probe:** `count_tokens` on the rubric prefix → confirms whether the 4096-token
   cache floor is cleared. Record the number.
3. **Correctness A/B:** run `python -m scorer.label --relabel --sample 100 --out api_labels.jsonl`
   through the **API** backend and the **CLI** backend on the same 100 rows; diff the scores. Accept
   only if the score distribution matches (same tolerance as any rubric change).
4. **Cost A/B:** compare real `usage`-derived API dollars (with batch + cache) against the CLI
   `total_cost_usd` estimate for the same batch. Record the **actual** multiple — this replaces the
   "120x" guess with a measured figure.
5. **Flip scoring only:** set `SCORING_BACKEND=api` for the bulk labeling/relabel jobs; leave watcher
   + verify_high + documents on `cli`. Watch the budget ledger + Telegram for a week.
6. **Log it:** `docs/CHANGELOG.md` entry (what/where/why + measured result); if the scoring path is a
   new subsystem-level change, update `docs/PROJECT_STATE.md` in the same commit (per its rule).

---

## 8. Risks & non-goals

- **Non-goal:** removing OAuth. Both backends stay; `cli` remains the default. This is additive.
- **Non-goal:** touching any prompt or rubric. Delivery transport only.
- **Risk — silent quality drift:** the API path is a different code route to the same model; a subtle
  difference (system-prompt handling, thinking config, JSON escaping) could shift outputs. Mitigation:
  §7 A/B gate, and parse tool/JSON output with `orjson`, never string-matching.
- **Risk — cache assumption:** if the rubric is under 4096 tokens, caching does nothing on Haiku.
  Mitigation: measure first (§7.2); the batch 50% stands regardless.
- **Risk — budget ledger races:** advisory only; the Console spend limit is the real cap.
- **Risk — two ways to score:** more config surface (`claude_backend`, `scoring.backend`,
  `api.budget_usd`). Mitigation: sane defaults (`cli`, inherit, $10) so an untouched deploy behaves
  exactly as today.

---

## 8b. Shared batching service — does cross-user pooling save? (mostly no)

Proposed idea: a Docker "scoring-batch service" (like the embedder service in
`EMBEDDER_SHARING_PLAN.md`) with its own HTTP API, that micro-batches Haiku calls **across multiple
users** and fans results back per user — pooling the batch discount and the cached rubric.

**The blocking fact:** Anthropic scopes the **batch discount, prompt cache, and rate limits per
organization / API key**. A batch runs under exactly one key and is billed to that key; a cache entry
is readable only by the key that wrote it. **You cannot merge requests from different users' API
accounts into one discounted batch, and you cannot share one user's cached rubric prefix with another.**
There is no cross-account leverage to capture.

### Why the embedder pools but a Haiku batcher does not

The embedder is a **deterministic, stateless, un-metered** artifact — same text → same vector for
everyone, so one loaded copy genuinely serves all instances (it pools *RAM/compute*). Haiku scoring is
**metered per account** — there is no shared, reusable artifact to amortize, so there is nothing
billable to pool. The embedder shares a *thing*; a Haiku batcher would only share a *pipe*.

### What each billing model actually yields

| Billing model | Cross-user pooling saving | Notes |
|---|---|---|
| **BYOK** — each user brings their own key/credits | **None** | Batch/cache/limits are per-key. Must run one batch per key. Pooling saves zero tokens. |
| **One shared org key** — you fund, you bill (SaaS) | **Minor** | One batch across tenants; rubric cache-written once not N times; shared rate pool. This is the pooled-broker model — designed in full in [POOLED_CLAUDE_ACCOUNT_PLAN.md](POOLED_CLAUDE_ACCOUNT_PLAN.md) (prepaid wallet, metering, reconciliation, ToS gate). |

### Where the real saving lives (no shared service needed)

Each user batches **their own** jobs under **their own** key → `-50%` batch + their own rubric cache.
That ~2x is captured **per instance**, by the phase-1 API backend (§3–§5), with no shared service on
top. A shared service adds **no token savings** to this.

### What a shared batching service IS good for (operational, not cost)

The same value the embedder gives — one process instead of N cron jobs:
- Orchestration: accumulate each key's jobs in a short micro-batch window → submit one Anthropic Batch
  **per key** → poll → fan results back by `custom_id`.
- Central retry / observability / budget ledger across instances.
- Fewer moving parts on a shared VPS.

It does **not** reduce tokens; it reduces ops. Build it only if already running multiple instances and
the orchestration is worth it — exactly the embedder's rationale.

### Security cost of centralizing BYOK keys

Pooling BYOK would require the service to **hold every user's Anthropic API key** — secret custody,
per-key rate buckets, blast radius on leak — for a near-zero pooling benefit. **Keep keys per-instance.**

**Verdict:** micro-batching across users to share Haiku savings is **not realistic under BYOK** — the
API's per-org scoping forbids it. Realistic wins are (a) per-user batch+cache under each own key
(build it, ~2x), and (b) a shared service purely as an orchestrator (optional, ops-only). Genuine
cross-tenant pooling requires a single shared org key, which is the SaaS/reseller model, not BYOK.

---

## 9. Summary

Add `claudeapi.py` + a `claudeback.py` dispatcher behind a `CLAUDE_BACKEND` switch (default `cli`),
move **only the bulk scoring/labeling** path to the API in phase 1 using the **Batch API (-50%)** and
**prompt caching (if the rubric clears Haiku's 4096-token floor)**, enforce a **client-side budget
ledger + Console spend limit**, and gate the flip on a **score A/B** and a **measured cost multiple**.
OAuth stays the default and keeps owning document generation. The honest expected win is roughly
**2x–5x cheaper scoring tokens plus quota separation**, not 120x — and the plan is built to *measure*
the real number before committing.
