# Profile capture — how a new user becomes a configured tenant

**Status:** design, 2026-07-26. Closes gap **G1** (`GAPS_REVIEW.md`), which blocks Phase 2.
**Resolves:** the forms-vs-interview question raised in `SAAS_PLATFORM_PLAN.md` §2 and left open.
**Decided 2026-07-26:** capture runs as a **Managed Agents** session (Anthropic hosts the loop and a
sandboxed container); credentials are collected before capture, on a free plan.

> ### ⚠️ Superseded in part, 2026-07-28 — §3, §4.1–4.5 describe a design that no longer ships
>
> **The sandbox is gone. Capture is one plain Messages call** (`services/capture`), built
> 2026-07-28. §5 onwards — confirmation, deterministic rendering, generation invalidation — is
> unchanged and still authoritative; it is the *how the interview runs* half that changed.
>
> What forced it: onboarding stopped uploading files. Text is extracted server-side at upload
> (`libs/documents`), so there was nothing left to mount, and three things followed at once —
>
> 1. **OAuth became possible.** Managed Agents authenticates by API key only, so §4.1's
>    "consequence worth stating" was in fact a contradiction of decision D2: a subscription
>    token is meant to be equally sufficient, and it would have failed at the last step of
>    onboarding with a message about a beta the user has never heard of.
> 2. **A beta left the critical path.** Nothing to pin, nothing to break under a running signup.
> 3. **Deletion became provable.** No Files API objects outside Postgres means no second cleanup
>    path to keep in step with the cascade (`ROADMAP.md` Phase 3 step 11).
>
> What was lost, honestly: the agent no longer *explores* the documents with glob and grep, and
> no longer asks follow-up questions mid-interview. It reads everything in one pass. §1's
> argument for an interview over a form still holds — the model still derives the NOT-list,
> which is the part a form structurally cannot do — but the multi-turn clarification it
> describes is not there. If the first cohort's profiles show unresolved conflicts piling up in
> `conflicts[]`, a follow-up turn is the cheap fix, not a return to the sandbox.

---

## 1. Why this is not "a signup form"

Onboarding today is `/setup` — a 472-line interactive Claude Code interview that reads the user's
documents and writes **nine artifacts**:

| Artifact | Consumed by | Quality impact if wrong |
|---|---|---|
| `CLAUDE.md` (~434 lines) | Ambient grounding on every generation call | **Fabrication** |
| `scorer/prompts/label.md` → "WHO <user> IS" (29 of 103 lines) | Every fit score, every training label | Feed becomes noise |
| `cv/base_cv_<role>.html` × 2 | Starting point of every tailored CV | Every CV mis-shaped |
| `01-candidate-profile.md` | Cover letters, outreach | Weak, generic prose |
| `02-behavioral-profile.md` | Interview prep | Low value |
| `04-job-evaluation.md` | Fit criteria | Redundant with the rubric |
| `05-cv-templates.md`, `07-interview-prep.md` | Document rules | Mostly platform-owned |
| `pipeline/config.yaml`, `.env` | Pipeline knobs | Covered by the config form |

A web form produces the last row — the least important.

### The three things a form structurally cannot do

1. **Derive what the user is *not*.** The rubric's hard-gate power comes from its NOT-list — "no
   professional experience in enterprise Java/C#/.NET at depth, mobile, embedded, game engines,
   Ruby/Rails, PHP at depth". Nobody self-reports that accurately. It was derived by reading a CV and
   reasoning about absence, and it is what makes an off-stack senior role score 20 instead of 60.
2. **Resolve contradictions and industry-dependent ambiguity.** `/setup` Step A4 cross-references
   documents and asks which version is right. A form takes whatever is typed, including the wrong
   thing.
3. **Ask the follow-up.** What matters next depends on the previous answer — a quant background and a
   frontend background need different questions. That is a conversation, not a field list.

### The two failure modes, and why one is worse

- **Bad rubric → bad labels → bad classifier → a noisy feed.** Recoverable, but *silent*: the user
  concludes the product does not work and leaves without reporting anything.
- **Bad grounding → fabrication.** The 2026-07-15 A/B measured this directly: stripped of profile
  context, Haiku invented an employer and fabricated metrics. In a hosted product that lands on a
  real application under the user's own name.

## 2. The shape: an agent captures, deterministic code renders

**A Managed Agents session conducts the interview; a plain renderer turns its output into artifacts.**
That split is the whole design and it is not negotiable in either direction.

```
CAPTURE  (agentic, non-deterministic)      RENDER  (deterministic, no LLM)
─────────────────────────────────────      ──────────────────────────────
 sandboxed session, user's documents         rubric_md      ← template + profile
 conversation, follow-ups, conflicts   ──▶   facts block    ← template + profile
 → users.profile (JSONB), user-confirmed     base CV        ← template + profile
                                             config.yaml    ← template + profile
```

**Why rendering must stay deterministic:** `profile_version` is `sha256(rubric_md)` and it scopes
which labels a training run may use. If an agent freely authors rubrics, that hash stops being
reproducible, the SACRED template drifts per user, and two users' rubrics become structurally
different — at which point you can no longer A/B a template change or reason about scoring globally.

**Why capture must be agentic:** everything in §1.

This split is also the **security boundary**. An uploaded CV is untrusted text and a genuine prompt-
injection surface. Injection cannot corrupt a user's hard gates, because gates come from **confirmed
structured fields**, not from agent free-text. The confirmation step (§5) does double duty as the
quality gate and the injection containment.

## 3. Why Managed Agents rather than a self-hosted agent

The alternative was the Claude Agent SDK in a container you operate. Managed Agents wins here because
**Anthropic hosts the sandbox**, and the containment this task needs is configuration rather than
infrastructure:

| Requirement | Mechanism |
|---|---|
| Restricted tools | `agent_toolset_20260401` with `default_config: {enabled: false}` and an explicit per-tool allowlist |
| Only the user's own files | Their documents uploaded via the Files API and mounted **read-only** at a `mount_path` |
| No network roaming | Environment `networking: {type: "limited"}` — deny-by-default egress |
| Approval gates | `permission_policy: {type: "always_ask"}` per tool; the session idles and waits for a `user.tool_confirmation` |
| Credentials never in the sandbox | Vaults — substituted at egress. **We attach none**, since capture needs no outbound calls |
| Reproducible setup | The agent object is persisted and versioned |

Zero container infrastructure to run, secure, or patch. Given the platform is otherwise entirely
self-operated, that is the deciding factor.

**Managed Agents is beta and Claude-API-only** — not available on Bedrock, Vertex, or Foundry. Not a
constraint today; record it as a dependency.

## 4. The capture session

### 4.1 Ordering — credentials come first

```
signup → verify email → free plan activated → credentials entered & validated → PROFILE CAPTURE
                                                                              → config + live match count
                                                                              → cold start (COLD_START_PLAN)
```

Credentials are collected **before** capture because the capture session runs on **the user's own
Claude credential** — their onboarding, their spend, no platform cost and no abuse surface. It also
validates the credential at the cheapest possible moment: a bad key fails at a form field instead of
at 3 a.m. mid-backfill.

**Consequence worth stating: capture requires an API key specifically.** Managed Agents is an API
surface; the OAuth path (decision D2) cannot drive it. A user who intends to run OAuth-only must
still supply an API key to onboard, or the platform must fund capture for them. Make this explicit at
the credential step rather than letting it surface as a confusing failure.

**Consequence worth stating: agent objects are per-user.** The client is constructed with the user's
key, so the agent object is created in *their* Anthropic workspace, not a shared one. The
"create the agent once, reference it forever" rule still holds — but *once per user*. Store
`users.capture_agent_id` on first capture and reuse it on re-capture; never create an agent per
session.

### 4.2 Agent definition (created once per user, versioned)

```jsonc
{
  "name": "Profile Capture",
  "model": "claude-opus-5",
  "system": "<platform-owned capture prompt>",
  "tools": [{
    "type": "agent_toolset_20260401",
    "default_config": { "enabled": false },          // deny by default
    "configs": [
      { "name": "read",  "enabled": true },          // read the user's documents
      { "name": "glob",  "enabled": true },
      { "name": "grep",  "enabled": true },
      { "name": "write", "enabled": true,            // write the profile JSON out
        "permission_policy": { "type": "always_ask" } }
    ]
  }]
}
```

`bash`, `edit`, `web_search`, and `web_fetch` stay **off**. Capture needs to read documents and write
one output file; nothing else. `write` is gated with `always_ask` so the backend approves each write
and can reject a path outside the outputs directory.

### 4.3 Environment

```jsonc
{ "name": "profile-capture",
  "config": { "type": "cloud",
              "networking": { "type": "limited" } } }   // no allowed_hosts — no egress at all
```

Capture makes no outbound calls, so the host allowlist stays empty. `allow_package_managers` and
`allow_mcp_servers` remain false.

### 4.4 Session

Documents uploaded via the Files API, mounted read-only:

```jsonc
{
  "agent": "<users.capture_agent_id>",
  "environment_id": "<env id>",
  "resources": [
    { "type": "file", "file_id": "file_…", "mount_path": "/workspace/docs/cv.pdf" }
  ],
  "initial_events": [
    { "type": "user.message", "content": [{ "type": "text", "text": "<kickoff>" }] }
  ]
}
```

Client mechanics that are easy to get wrong, all from the SDK contract:

- **Open the event stream before sending anything.** The stream only delivers events emitted after it
  opens; send-then-stream loses the opening events.
- **Break correctly.** Exit on `session.status_terminated`, or on `session.status_idle` whose
  `stop_reason.type` is *not* `requires_action` — a plain idle-break stops mid-interview while the
  agent is waiting on the user.
- **Reconnect losslessly.** SSE has no replay; on reconnect fetch `events.list` and dedupe by event
  id before tailing the live stream.
- **Cap the session.** A wall-clock timeout and a turn ceiling, both enforced by the backend. An
  interview that will not converge must fail into the manual-entry fallback, not run forever on the
  user's credit.
- **Archive the session** when capture completes.

### 4.5 Output — a file, not chat text

The agent writes `/mnt/session/outputs/profile.json` and the backend downloads it via the Files API
(`scope_id` = the session id). **Do not parse the profile out of conversational text** — a file is
schema-validatable, unambiguous, and does not break when the agent adds a friendly sentence.

Expect a 1–3 s indexing lag between the session going idle and the file appearing; retry the listing
once or twice. Note the listing call needs the Managed Agents beta alongside the Files beta.

Schema:

```jsonc
{
  "identity":     { "name", "location", "work_eligibility": [], "links": {} },
  "education":    [{ "degree", "institution", "field", "thesis" }],
  "experience":   [{ "role", "org", "start", "end", "bullets": [], "stack": [], "metrics": [] }],
  "projects":     [{ "name", "what_built", "stack": [], "metrics": [] }],
  "skills":       { "languages": [], "frameworks": [], "domains": [], "tools": [] },
  "targets":      { "role_types": [], "seniority", "company_size" },
  "constraints":  { "remote_policy", "onsite_locations": [], "languages": [], "min_salary" },
  "not_list":     ["<domain with no professional evidence in the documents>"],
  "conflicts":    [{ "field", "value_a", "source_a", "value_b", "source_b" }],
  "exclusions":   ["<entry the user does not want on any CV>"]
}
```

Three fields carry the weight:

- **`not_list`** — the agent is instructed to name domains with *no* supporting evidence. This is how
  the NOT-list gets derived instead of self-reported. It is a proposal; §5 confirms it, and it is the
  field to review hardest, because a wrong entry becomes a hard gate that silently suppresses good
  jobs.
- **`conflicts`** — the agent should resolve most of these in conversation. Anything it could not
  resolve surfaces in §5 as an explicit either/or.
- **`exclusions`** — the reference instance's "never put Freelance Web Developer on a CV" case,
  generalised.

Validate against the schema on download. A malformed or missing file falls back to manual entry.

## 5. Confirmation — the step that must not be skipped

The captured profile is rendered as an editable form before anything downstream uses it.

- Experience and project entries editable inline, reorderable, deletable.
- **Unresolved conflicts as explicit either/or choices**, showing both sources.
- **The NOT-list shown with a plain-language explanation** — "we will not send you roles whose core
  requirement is one of these" — and freely editable. Users understand it once it is framed as
  consequences rather than as a skills gap.

**The confirmed output is the single source of truth**, stored as `users.profile` (JSONB). It is also
the injection boundary: nothing the agent produced reaches a hard gate without passing through a human
click.

## 6. Rendering — deterministic, no LLM

Every artifact is a template fill. This is what makes profile changes cheap, reproducible, and
diffable, and keeps the model out of the path where consistency is the whole requirement.

### 6.1 Rubric

```
label_template.md  +  users.profile  ->  users.rubric_md
                                          users.profile_version = sha256(rubric_md)[:8]
```

The template owns the scaffolding (decision procedure, score anchors, calibration examples, output
contract); the profile fills only the "WHO <user> IS" block and the hard-gate parameters:

| Rubric element | Source |
|---|---|
| Seniority band + years tolerance | `targets.seniority` + education |
| Strengths, with evidence | `experience` + `projects` + `skills` |
| NOT-list | `not_list`, confirmed in §5 |
| Targets, priority order | `targets.role_types` |
| Remote / on-site / relocation gates | `constraints` |
| Language gate | `constraints.languages` |

**The template is SACRED** under the same rule as the generation prompts (`job-search/CLAUDE.md` →
Prompt & Rule Change Control): no edit without explicit approval, A/B measured, changelogged. Editing
it changes **every** user's `profile_version` and invalidates **every** user's labels — so template
changes must be able to relabel in the background rather than blanking everyone's model at once.

Calibration examples are the open sub-problem: the reference instance's are written against Ruben's
stack ("2+ core overlaps"). Either express them relative to the profile ("a role with 2+ of *this
user's* core overlaps → 93") or generate a small set per user at render time. Prefer the former — it
keeps rendering deterministic. Validate on the first cohort.

### 6.2 Document grounding

The `CLAUDE.md` equivalent is a **facts block** assembled from `users.profile` and injected explicitly
into every generation call. Non-negotiable, per the fabrication A/B — not an optimisation to defer.
In a hosted service there is no CLI auto-loading a file, so this must be built deliberately.

Represent it as a file if that helps (readable, diffable, agent-editable), but **the database is the
source of truth** — materialise to scratch when a session needs it, write back on completion.

Also derived here: the CV Header Location Rule (from `work_eligibility`), the Languages line, and the
exclusions list.

### 6.3 The feature extractor — the third personal artifact

**`scorer/features.py` is personal too, and this was missed in every earlier draft.** It hardcodes the
operator's stack:

- `_CORE_TOKENS` — the strengths list, driving `core_stack_count`
- `offstack_count` — counts hits against **the NOT-list**
- `region_lock_flag`, `junior_flag`, `senior_flag` — calibrated to one candidate's constraints

So the profile renders **three** artifacts, not two: rubric, facts block, **and the feature token
lists**.

It also carries a constraint stated in its own docstring:

> *training and serving MUST build these identically… The vector length and order are fixed by
> `FEATURE_NAMES`; append new features at the END so older cached artifacts stay readable.*

Two consequences:

1. **The extractor must be built from `users.profile` at both train and serve time**, from one shared
   function. Two copies that drift is a silent scoring corruption, not a crash.
2. **`FEATURE_NAMES` order stays platform-owned and global.** Only the *token lists* are per-user. If
   the column order varied per user, no artifact would be portable and the append-only rule would be
   meaningless.

### 6.4 One invalidation story, not two

A profile edit now invalidates two different things, and they must be handled together:

| What changes | Invalidates | Because |
|---|---|---|
| Rubric-relevant fields | **Labels** | `profile_version = sha256(rubric_md)` scopes which labels a training run may use |
| Core-stack / NOT-list tokens | **The trained artifact** | `core_stack_count` and `offstack_count` now *mean* something different; the old model's weights were fit against different semantics |

The failure mode if these are tracked separately: a profile edit bumps `profile_version`, a relabel
runs, a retrain runs — but the serving path still holds a model fit against the *previous* token
lists, and nothing errors. Scores are quietly wrong.

**Fix: one `profile_generation` counter covering both.** Bump it when either the rubric-relevant
fields or the feature token lists change. Stamp it on labels *and* on the model artifact; the scorer
refuses to serve when its artifact's generation does not match the user's current one — the same
fail-closed discipline the embedder already applies to `(model, dim, version)`.

### 6.5 Base CV templates — buy, do not generate

**Platform-maintained templates filled from the profile. Do not generate HTML per user.**

The reference instance has two hand-built, heavily-tuned templates (`base_cv_dev.html`,
`base_cv_quant.html`) — A4 page rules, font paths, section order, the `@page` margin rule that
prevents Chrome header overlays. Generated-per-user HTML would be unpredictable exactly where
predictability is the requirement, and every layout bug becomes a per-user bug.

Keep 2–3 platform templates; the user picks a starting point. Per-job tailoring still happens per
build, as today.

## 7. Profile changes

```
user edits profile
  -> re-render rubric -> new profile_version
  -> enqueue background relabel of their labelled set (label lane)
  -> retrain when relabel completes
  -> facts block picked up on next generation call (no rebuild needed)
```

Never mix rubric generations in one training set — `SCORER_PROFILE` already enforces this in the
reference instance and it must stay true per user.

**Only re-version when the rubric-relevant subset changes.** Hash that subset separately from the full
profile, so a new phone number does not trigger a relabel.

Re-running capture reuses `users.capture_agent_id` and starts a fresh session with the existing
profile as context.

## 8. Cost, timing, and failure

| Step | Cost | Time |
|---|---|---|
| Upload + session start | 0 | seconds |
| Agent interview | User's own credential; an agentic session is materially more expensive than one call — **measure on the first cohort and publish it as the free-plan cost** | ~5–15 min including user turns |
| Confirmation | 0 | ~5 min user time |
| Render | 0 | instant |

| Failure | Response |
|---|---|
| Session exceeds turn/time cap | Terminate, keep partial profile, fall back to manual entry |
| `profile.json` missing or malformed | Retry listing (indexing lag), then manual entry |
| User's credential invalid | Caught at the credential step, before capture |
| User's credential exhausted mid-session | Save partial, notify by email, resume when quota resets |
| Agent requests a disallowed write | `always_ask` gate rejects; logged |
| Session hangs | Wall-clock timeout; admin sees it in the queue view (`OPERATIONS_PLAN.md` §5) |

**Capture must never be a hard blocker.** Manual entry is always reachable — a user who cannot get
through the interview must still be able to onboard.

## 9. Open questions

1. **Calibration examples per user** (§6.1) — relative phrasing vs generated set. Decide on the first
   cohort's measured label quality.
2. **How much does profile quality actually move scoring?** Worth measuring on the first two real
   users: render a rubric from their profile, label 100 jobs, compare against hand-checked scores.
   `SCORING_RUBRIC_GUIDE.md`'s method applied to a generated rubric.
3. **Multi-language profiles.** The reference instance builds Hungarian CVs for Hungarian postings.
   The facts block would need per-language rendering. Defer past v1, but do not design it out.
4. **Capture cost on the free plan.** Unknown until measured. If it is material, the free plan may
   need a capture-attempt cap.
