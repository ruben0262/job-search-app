# Onboarding, processing, and training — where the LLM runs and where it does not

## Implementation status — audited 2026-07-31

**Implemented.** Both live accounts went through this flow, and it is verified in a browser rather
than asserted.

- **§1's rule holds:** the server decides which page a user is on. `/dashboard` answers
  `stage: "onboarding"` with the page list, so the client never re-derives "are they done yet"
  from a pile of booleans.
- **§2's pages** are built, as numbered step circles with working back-navigation, and every
  answer is editable afterwards on the **Your details** page — which renders the same components
  the wizard does, so a correction behaves exactly like getting it right the first time.
- **§3's config-as-sections** is live, and the form was rebuilt on 2026-07-30 for people who do
  not read forms: chips instead of newline-separated textareas, grouped checkboxes instead of a
  `<select multiple>`.
- **§4's "state, not buttons"** is the dashboard: one endpoint reads the counters and says what
  the account needs now, because the order is not guessable and pressing them wrong costs money.
- **§5's LLM-first labelling with behaviour as the corrective** is `services/labeller`.
- **§7 explains why the sandbox went away**, and that reasoning outlived the section: Managed
  Agents is API-key-only and would have silently excluded every OAuth user.

Two departures worth knowing: the interview is now the setup chat of `REBUILD_PLAN.md` rather
than a capture call, and a turn runs as a queue job (`services/setupagent`) rather than inside the
request — an HTTP request that takes minutes dies whenever the browser navigates.


**Status:** decided 2026-07-28. Supersedes the onboarding ordering sketched in
`PROFILE_CAPTURE_PLAN.md` §4.1 and the "capture during onboarding" assumption throughout that
document. The split it describes between agentic capture and deterministic rendering is unchanged
and still correct — what moves is *when* the agentic part runs.

---

## 1. The rule

**Onboarding collects data. It never calls an LLM.**

Everything a user does before reaching the dashboard is a form, a file upload, or a credential
check. That buys four things worth having:

1. **Onboarding cannot fail because a provider is down.** The only external calls are credential
   validations, and an unreachable provider stores the credential unvalidated rather than blocking.
2. **It costs nothing.** No user spends their own credit before they have seen the product work.
3. **It finishes in one sitting**, with no step that takes minutes and might not converge.
4. **The failure modes are ordinary form failures**, which are the kind a non-technical user can
   recover from unaided.

The LLM runs in exactly three places, all of them *after* onboarding, all of them user-triggered:

| Where | What it does | Cost |
|---|---|---|
| **Process data** | Reads the uploaded documents and the config, produces the confirmed profile JSON | User's credential, once per change |
| **Relabel** | Re-scores the user's jobs under a new rubric | User's credential, per job — **the expensive one** |
| **CV / cover generation** | Writes the document, grounded by the facts block | User's credential, per build |

A possible fourth — *training optimisation*, where a model with poor holdout metrics is diagnosed
and the fix suggested — is deliberately left out of v1. Everything else on the platform is
deterministic: harvesting, dedup, embedding, feature extraction, scoring, quotas, delivery.

## 2. Onboarding, page by page

Every page is required. Nothing is skippable, because a half-configured account produces a bad feed
and the user concludes the product does not work.

| Page | Collected | Notes |
|---|---|---|
| 1. Identity | Full name as it appears on a CV; links as (URL, platform) pairs | The name is not the account email — it is what a recruiter reads |
| 2. Documents | CV, cover letters, experience docs, references, achievements, diplomas, LinkedIn export | Text extracted server-side on upload; the originals are ephemeral (D9) |
| 3. History | One long-form write-up of everything they have done | **Typed, not uploaded** — nobody has this document already. Stored as a `history` document all the same, so processing has one input path |
| 4. Telegram | Bot token; **chat id captured automatically** via `getUpdates` | The chat id is the step that confuses people most, so it is never asked for |
| 5. Claude | API key **or** OAuth token — either satisfies it (D2) | An API key needs credit; the form says so before they paste one |
| 6. Proxy | Proxy credential for the LinkedIn tier (D3) | Validated by fetching through it, not just by connecting |
| 7. Config | Search sections — see §3 | Captured here so the first harvest is useful rather than empty |

After page 7 the user reaches the dashboard for the first time. Everything above is editable there;
onboarding is the only place it is *sequenced*.

**Completion is derived, not recorded** (`libs/onboarding/state.py`). No row says "page 4 done" —
each page's state is computed from what that page writes. A stored progress flag would be a second
source of truth, and it goes stale the moment somebody revokes a credential from the account page:
the account would read as finished while its feed could no longer be delivered. Deriving it means
revoking a bot token reopens the Telegram page, which is the truth.

**Finishing does not start the notifier.** `users.active` is what lets the scheduler dispatch, and
it stays false until a model exists to rank with — otherwise every cycle fails identically and
invisibly. The dashboard's next instruction after onboarding is *process data*, not *wait*.

**Abandoned signups purge.** Documents belonging to an account that never finished are deleted after
a retention window — D9 makes generated documents ephemeral and the same reasoning applies harder to
uploaded ones, which the platform did not create and has no claim to keep.

## 3. Config as sections

The flat config (one keyword list, one location list) is replaced by **sections**, because a user
who wants different terms on LinkedIn than on the job boards currently cannot express that.

```yaml
sections:
  - platform: all            # the default; most users never add another
    keywords: [...]
    locations: [...]
    workplaces: [remote, hybrid]
    posted_within_hours: 24
  - platform: linkedin       # optional, added from a dropdown
    keywords: [...]
    workplaces: [remote]
```

This fits the harvester rather than fighting it: `QueryKey` already carries `source`, so a section
naming a platform simply emits queries for that source and no other. The fold, the fan-out budget,
and round-robin selection are unchanged.

**Time filter.** 24 hours is the right steady-state default — the harvest cycle runs every 15
minutes and a wider window mostly re-reads jobs already deduplicated away. But a user who signs up
today has an empty feed, and a 24-hour window on day one shows them almost nothing. So: **24 hours
for ongoing harvest, one wider pass at cold start** (`COLD_START_PLAN.md`), narrowing afterwards.
The alternative is that the first impression of the product is a blank page.

## 4. Process and train — state, not buttons

The two actions are coupled **in one direction only**, and the dashboard shows which state the
account is in rather than offering two buttons and hoping they are pressed in order.

```
documents or profile changed   →   PROCESS DATA      (spends credential)
                                        ↓ bumps profile_generation
                                   RELABEL           (spends credential, per job)
                                        ↓
labels changed, or stale       →   TRAIN MODEL       (free — CPU only)
                                        ↓
                                   READY
```

**Process implies retrain. Retrain does not imply process.** The first direction is already enforced
by the code: a new profile bumps `profile_generation`, which makes every label and the model stale,
and the scorer refuses to serve an artifact whose generation does not match.

The second direction must **not** be enforced, and this is the decision worth recording. Retraining
is needed when *labels* change — a user who has been applying and skipping for two weeks has new
feedback labels and no profile change at all. Forcing a re-process there would spend their credit
re-deriving a profile from unchanged documents, bump the generation, and **invalidate the very
labels the retrain was meant to use**. It would discard the training data in order to train.

The dashboard reads the counters it already has:

| Condition | What the user is told |
|---|---|
| `documents_changed_at > processed_at` | "You have added documents. Process them to update your profile." |
| `users.profile_generation > model.profile_generation` | "Your profile changed. Your jobs need relabelling, then a retrain." |
| `labels_since_last_train >= TRAIN_MIN_NEW_LABELS` | "You have labelled N jobs since your model was trained." |
| otherwise | "Ready." |

**Warnings before each action, and they name consequences rather than being generic.** Before
processing: *"this replaces your profile and every job you have scored will need relabelling —
about N jobs, roughly $X on your key"*. Before training: *"this uses the labels you have now"*. A
warning that says "are you sure?" is one nobody reads.

## 5. Labels: LLM first, behaviour as the corrective

The initial label set comes from Haiku scoring jobs against the rendered rubric. What the user then
*does* — building a CV, applying, skipping — is the higher-quality signal, and the schema already
records it: `user_labels.source = 'feedback'`, `weight = 5`, applied → 90, skipped → 10.

**The known tradeoff, recorded rather than solved.** A user applying to everything out of desperation
teaches the model that everything is a match. There is no general fix — the behaviour genuinely is
their revealed preference at that moment — but three mitigations are cheap, and all three are open
decisions rather than defaults:

1. **Recency weighting.** A desperate fortnight two months ago should not outweigh how they are
   behaving now. Decay `weight` by age at train time.
2. **Cap feedback as a proportion of the training set.** Keep LLM labels as the backbone so a burst
   of indiscriminate applications cannot dominate.
3. **Separate the two signals.** *Built a CV* and *confirmed applied* are currently the same row at
   the same weight; the second is a much stronger statement than the first.

Decide these against the first cohort's measured label quality, not in advance — the reference
instance's flat weight of 5 was itself arrived at by measurement.

## 6. What this changes in the existing plan

| Document | Change |
|---|---|
| `PROFILE_CAPTURE_PLAN.md` §4.1 | Capture no longer runs *during* onboarding. Credentials are still collected first, but capture is a dashboard action |
| `PROFILE_CAPTURE_PLAN.md` §3 | Managed Agents is no longer required. With documents extracted server-side there is nothing for a sandbox to read, which also makes the OAuth path viable — see §7 |
| `TENANT_SAFETY.md` §4 | Config becomes sections rather than a flat list; `schema_version` 1 → 2 |
| `ROADMAP.md` Phase 3 | Onboarding is one deterministic sequence; processing and training are dashboard actions |

## 7. Why the sandbox went away

`PROFILE_CAPTURE_PLAN.md` §3 chose Managed Agents so the agent could read the user's documents
itself, in a container, with a restricted toolset. That requirement disappears once the platform
extracts document text on upload: there are no files for the agent to read, only text in a
conversation.

What that removes: a beta dependency on the critical path, a per-session container, a tool-approval
loop, and the sandbox's whole containment surface.

What it enables: **OAuth**. Managed Agents is an API surface and cannot be driven by an OAuth token;
plain Messages calls can (`Authorization: Bearer` plus the `oauth-2025-04-20` beta header). Since
D2 requires supporting both credential types, removing the sandbox is what makes that promise
keepable.

What it costs: the agent can no longer explore a document set on its own — the platform decides what
text goes into the conversation. For CVs and reference documents that is not a real loss.

**Prompt injection is unchanged.** An uploaded CV is still untrusted text, and the boundary was never
the sandbox: it is that nothing the model produces reaches a hard gate without passing through a
human confirmation and a strict schema (§5 of the capture plan). That holds identically here.
