# Cold start — from signup to a working feed

## Implementation status — audited 2026-07-31

**Built.** Signup → working feed exists end to end and is the flow both live accounts went
through. The labeller still cites this document for its live numbers, which is why it is kept
rather than retired: `services/labeller/label.py::BATCH_SIZE` and `coldstart.ready_to_train` are
this plan's thresholds, and changing them means changing this text too.

What differs from the plan as written, and why:

- **The interview is the setup chat, not a capture call.** `REBUILD_PLAN.md` replaced the
  one-shot schema extraction with a conversation that writes the profile files directly — the
  earlier design spent one call on a schema and produced a profile with no career in it.
- **Labelling is per-user Haiku on the user's own key**, chunked 50 to a call. The Batch API's
  50% discount is available and unclaimed; nothing on this path is latency-sensitive.
- **The cutoff is no longer 60.** The trainer picks a classifier by bake-off and tunes its own
  decision cutoff (2026-07-31); 60 was the *label* threshold and was never an operating point.


**Status:** design, 2026-07-26. The highest-churn-risk part of the product: a new user with no labels
has no model, and a product that does nothing useful for three days loses them.

---

## 1. The advantage shared storage hands you

A new user does not start from an empty database. The moment they finish onboarding, the corpus
already holds **tens of thousands of historical postings** (65k in the reference instance) with
descriptions and embeddings already computed. So the cold start is not "wait for the scraper" — it is
"select from what we already have".

That reframes the whole problem:

```
signup → filter corpus by their config → sample a balanced pool
       → label the pool (Claude) → train → personal model live
```

Every step is a database query plus one bounded LLM spend. No waiting on scrapes, no empty feed.
**This is the single strongest reason the shared-corpus architecture is right**, beyond the cost
savings.

## 2. Three-stage onboarding, so the feed is never empty

| Stage | When | What scores their jobs | Quality |
|---|---|---|---|
| **0 — rules only** | immediately on config save | Config filters alone (keywords, location, remote, salary, seniority) | Rough but non-empty. User sees their feed working within seconds |
| **1 — bootstrap model** | within ~1 min | A generic classifier trained on the union of consenting users' labels | Decent. Catches obvious mismatches |
| **2 — personal model** | after backfill labelling + first train (~10–30 min) | Their own classifier | The product |

The user is never staring at nothing. Stage 0 is free, and it is what turns a 30-minute cold start
into a 30-second first impression.

**Consent note:** the bootstrap model in Stage 1 is trained on other users' labels. That is a data-use
question, not just an engineering one — it needs an explicit opt-in at signup, or (simpler for v1)
train the bootstrap model **only on the operator's own labels** from the reference instance. Recommend
the latter for launch: no consent surface, no ambiguity, and 4,283 existing labels is plenty to seed a
generic "is this a plausible tech role" classifier.

## 3. The per-user rubric — the biggest hidden task

`scorer/prompts/label.md` is self-contained by design: it embeds "WHO RUBEN IS", the hard gates, the
decision procedure, and calibration few-shots. Its hash is `profile_version`, which scopes which
labels a training run may use (`SCORER_PROFILE`). That design is correct and should be kept — but it
is **single-tenant to the core.**

Multi-tenant needs:

```
users.profile           JSONB   -- structured answers from the signup form
users.rubric_md         TEXT    -- rendered rubric, the actual prompt sent
users.profile_version   TEXT    -- sha256(rubric_md)[:8], per user
```

- The rubric is **rendered from a template** — the stable scaffolding (score anchors, decision
  procedure, output contract, brevity rules) stays platform-owned and change-controlled; only the
  candidate-facts block is per-user.
- Changing the template changes every user's `profile_version` → invalidates every user's labels.
  Treat the template as **SACRED** under the same rule as the generation prompts
  (`job-search/CLAUDE.md` → Prompt & Rule Change Control): no edit without explicit approval, A/B
  measured, changelogged. Add a migration path — a template change must be able to relabel in the
  background rather than blanking everyone's model.
- A user editing their own profile bumps only their own `profile_version`, and enqueues a
  background relabel of their labelled set.

**This is the single largest de-personalisation task in the project and it is invisible in every prior
plan.** Budget it as a first-class workstream, not a config change.

The same treatment is needed for the document-generation prompts (`tailor_cv.md`, `tailor_cover.md`,
`outreach.md`, `interview.md`), which currently rely on `CLAUDE.md` auto-loading as the model's factual
grounding. In a hosted service there is no per-user `CLAUDE.md` on disk to auto-load — the grounding
must be injected explicitly from `users.profile`. Note the measured hazard: the 2026-07-15 A/B found
that removing that grounding caused Haiku to **fabricate employers and metrics**. So this cannot be
skipped or approximated; the facts block has to be assembled deliberately per user and per call.

## 4. Backfill pool selection

Labelling a random sample is wasteful — most jobs are obvious rejects and teach the classifier little.
Select for **balance and boundary**:

```
pool = 300 jobs, drawn from the user's config-filtered corpus slice:
   40%  high bootstrap-score      (likely positives — teaches what "good" looks like)
   40%  mid bootstrap-score       (the boundary — where the classifier actually learns)
   20%  low bootstrap-score       (clear negatives — calibration floor)
   spread across sources and across the last 90 days
```

If the filtered slice yields fewer than ~150 jobs, the config is too narrow. Two responses, in order:
warn the user at config time with a live match count (`TENANT_SAFETY.md` §4), and only then fall back
to a live targeted scrape.

**Cost, from measured figures:** the reference instance labels at ~$0.09 per 50 jobs with Haiku,
i.e. **~$0.0018/job**. A 300-job backfill is **~$0.54**. With the Batch API's 50% discount, ~$0.27.
Cold start is not a cost problem; it is a latency and UX problem.

## 5. Claude access: API key vs OAuth

### API key (recommended default — decision D2)

Every efficiency lever is available:

| Lever | Effect | Status |
|---|---|---|
| Batch API | **−50%** | Available; backfill is offline, ideal fit |
| Prompt caching on the rubric prefix | up to −90% on cached input | **Requires the prefix to clear Haiku's 4096-token minimum — still unmeasured.** Measure before relying on it |
| `max_thinking_tokens=0` | Thinking is billed at 5× output and was silently ~55% of labelling cost | Already in place |
| Description cap 2,500 chars | Requirements live in the first ~2,500 chars; the tail is EEO boilerplate | Already in place |
| Minified JSON, only rubric-scored fields | ~10% | Already in place |
| Batching many jobs per call | Amortises the rubric prefix | Already in place (chunk 50) |

Plus the properties that matter for a hosted product: predictable per-user cost, clean metering, and
failures that are ordinary HTTP errors.

### OAuth / subscription (opt-in, docgen only)

The brief asks what efficiency routes exist here that are not yet used. Honest answer: **strictly
fewer, and the gap is structural.**

Available: bare mode (skip auto-loading project context), replaced system prompt, thinking off,
description caps, multi-job batching per call. All already used by the labelling path.

**Not available:**
- **Batch API** — not reachable through the CLI/OAuth path. The 50% discount is simply off the table.
- **Explicit cache control** — already measured on the reference instance: the CLI only caches the
  *system prefix*, and an attempt to exploit it by reordering `tailor_cv.md` gave **zero** benefit
  (CHANGELOG 2026-07-20). Do not retry it; the negative result is recorded.

Two further problems specific to a hosted service:
- **Quota exhaustion is a hard stop with terrible UX.** A user hits their weekly subscription limit
  mid-backfill and the platform has no way to finish or to bill around it.
- **Anthropic's terms on using a consumer subscription inside a hosted third-party service are
  unverified.** Treat this as a real risk (`PLATFORM_ARCHITECTURE.md` §10.4), not paperwork.

### Resolution (decision D2, 2026-07-26): both supported, user chooses

Both credential types are first-class. The platform makes the trade-off visible rather than choosing
for the user.

```
users.claude_api_key_ref    TEXT NULL   -- encrypted
users.claude_oauth_ref      TEXT NULL   -- encrypted
users.claude_mode           TEXT        -- 'api' | 'oauth'  (the active choice)
```

- **A user with only one credential**: that one is used, no prompt.
- **A user with both**: a toggle in settings picks the active mode. Default to `api` on first save,
  since it is cheaper and cannot hit a subscription wall.
- **Warning copy is required, not optional**, shown at the point of choosing OAuth: it spends the
  user's own Claude subscription usage, it forgoes the 50% batch discount, and hitting the
  subscription limit stops work until their quota window resets. Users cannot make this trade-off
  without being told it exists.
- **Per-workload override.** The two workloads have genuinely different profiles, so the toggle is
  offered per workload rather than globally:

| Workload | Volume | Default when both present | Why |
|---|---|---|---|
| Backfill + ongoing labelling | High, offline, batchable | **API** | The batch discount only exists here; a subscription wall mid-backfill is the worst UX in the product |
| CV / cover / outreach / interview | Low, interactive | **User's choice, API preselected** | A quota stop is recoverable — the user retries later on their own document |

- **Fail-fast on both.** Each credential is validated with a real minimal call at save time
  (`TENANT_SAFETY.md` §2), so a bad key surfaces at the form rather than at 3 a.m. mid-backfill.
- **Build the API path first.** OAuth is a second adapter behind the same interface, not a parallel
  architecture. Ship API, then add OAuth — do not develop them together.
- **An API key is required to onboard at all.** Profile capture runs as a Managed Agents session
  (`PROFILE_CAPTURE_PLAN.md` §4.1), which is an API surface — the OAuth path cannot drive it. Say this
  plainly at the credential step; discovering it as a failure mid-signup is a bad first impression.
- **Unverified:** Anthropic's terms on using a consumer subscription inside a hosted third-party
  service. Because D2 now supports OAuth rather than avoiding it, this needs an actual answer before
  the OAuth path is offered beyond the invited cohort (`PLATFORM_ARCHITECTURE.md` §10.4).

## 6. Timeline target

| t | Event |
|---|---|
| 0 | Config saved. Stage 0 rules-only feed live |
| +30 s | Bootstrap model scores their backlog. Stage 1 feed live |
| +1 min | Backfill pool selected, `label` jobs enqueued |
| +10–25 min | Labelling completes (batch or sync depending on load) |
| +30 min | First personal train completes; `model_version` bumped; scorer reloads. Stage 2 live |

The user sees value at 30 seconds and a personal model inside half an hour. Publish the stage the user
is in on the dashboard so the progression is visible rather than mysterious.

## 7. Failure modes to handle explicitly

| Failure | Response |
|---|---|
| User's config matches <150 corpus jobs | Warn at config time with live match count; offer to widen; fall back to targeted live scrape |
| Invalid/expired Claude credential | Fail fast at save time with a real test call, not at backfill time |
| Backfill labelling partially fails | Train on what succeeded if ≥100 labels; retry the rest in the `label` lane |
| Claude spend cap hit mid-backfill | Stop, keep partial labels, notify user, resume on next cap window |
| User edits profile after training | Bump their `profile_version`, background relabel, retrain — never silently mix rubric generations |
