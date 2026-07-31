# Rebuild — markdown profiles, setup agents, and the reference instance's actual behaviour

## Implementation status — audited 2026-07-31

**Steps 1–4 of the order of work are done; step 5 is part done and is the only thing left here.**

| Step | State |
|---|---|
| 1. Storage and import | ✅ `user_profile_files`, and the founder's own `CLAUDE.md`, skill files and base CV imported directly |
| 2. Chat and the agent loop | ✅ Built, then rebuilt twice: a real chat UI on 2026-07-30, and the turn moved off the request path into the `setup` lane the same day. **One setup turn has run in production** |
| 3. Scoring and the feed | ✅ `fetch_desc`, shadow scores, refresh, `opportunity_score`, `/rate` — all present. The feed is a real table with filters and pagination |
| 4. Applications page and the retrain popup | ✅ The jobs table carries applied state and filters on it; the Model page owns training and says a retrain is needed rather than running one |
| 5. Delete the schema machinery | 🟡 **part done.** `templates/cv.py` and `docprompt.py` deleted 2026-07-31. `rubric.py`, `facts.py`, `services/capture`, the `process` lane, `ProcessPage.tsx`, the confirmation form and the generated types remain — that flow is *superseded but wired end to end and passing its tests*, so retiring it is a product cutover rather than a cleanup |

**The two buttons work and the loop is not closed.** `libs/feedback/actions.record` writes a
`user_labels` row with `source='feedback'` and `weight=5`, and the Telegram webhook calls it — but
production holds **3,499 `llm` labels and zero `feedback` labels**. The founder account carries 98
`applied_at` timestamps, all imported from the reference instance between 27 and 29 July, which
never went through `record()` and so never became training data.

So the flywheel this document is built around — *every CV you build is a positive label at 5×
weight* — has never turned in production. Two separate things to decide: whether to backfill
labels from those 98 real applications, and proving one live tap end to end.


Decided 2026-07-29. This supersedes the parts of `PROFILE_CAPTURE_PLAN.md` that describe a JSON
profile schema as the source of truth, and it is the plan the next session should follow.

---

## What went wrong

The platform reimplemented the reference instance instead of porting it. `ProfileSchema` — a JSON
shape — became the profile, and four renderers turned it into a rubric, a facts block, a CV and a
prompt. That is roughly 1,500 lines whose entire job is to produce worse versions of documents that
already exist as markdown in `job-search` and `mahoraga-jobs`.

The results were measurable, and all three were found by using it:

- **Capture read nine documents and produced zero experience entries.** The schema is what the model
  is handed, and its fields had no descriptions, so nothing asked for a career.
- **The first real CV was a stub** — an unstyled page with Chrome's own header printed over it,
  no contact details, and a skills list where the work should be.
- **The feed card was three lines** where the reference sends fit band, applicant count with its
  bucket colour, freshness, workplace mode, posted salary and the user's own ask band.

The reference instance's quality comes from ~2,183 lines of hand-written markdown — a profile
document auto-loaded on every call, seven skill files, and six task prompts. None of it was ported.

## What replaces it

Three classes of file, one rule each.

| Class | Lives in | Filled by | Files |
|---|---|---|---|
| **Setup agents** | repo, `libs/agents/` | nobody — they *are* the machinery | the nine `.claude/commands/*.md` |
| **Field-agnostic** | repo, `libs/prompts/` | nobody — identical for every user | `03-writing-style`, `06-cover-letter-templates`, `SKILL.md`, and the six task prompts |
| **Per-user** | **Postgres** | the setup agents, through the chat | `context.md`, `01-candidate-profile`, `02-behavioral-profile`, `04-job-evaluation`, `05-cv-templates`, `07-interview-prep`, `label.md`, `base_cv.html`, the feature token lists |

At call time: platform files + that user's files, concatenated in a fixed order. That is what
`cwd=REPO_ROOT` does for the reference instance, done explicitly.

**mahoraga-jobs is the canonical clean set.** Its nine agent files are byte-identical to
andras-job-search's, and the five skill files that differ between them differ only by
personalisation. Copy from mahoraga-jobs; use Andras's filled copies as a worked example of what
"filled" looks like.

`ProfileSchema` survives only where a machine needs a value it can filter on — languages, regions,
salary floor, on-site locations, search terms. Same split the reference has: `.env` and
`config.yaml` for knobs, `CLAUDE.md` for prose.

## The setup chat

A page, not an IDE. The agent's tools are four server-side functions: `list_documents`,
`read_document`, `read_profile_file`, `write_profile_file` — the same Read/Write/Glob/Edit surface
`/setup` actually uses, and nothing more. Message list, input box, streamed replies, and a
right-hand list of the user's files that can be opened and edited by hand.

Slash commands in the box (`/setup`, `/setup-prompts`, `/setup-scorer`) load that agent as the
system prompt, so the questions are the same for every user. Sessions are stored, so setup resumes
and can be re-run later to update.

**Model: `claude-opus-5`.** Setup runs once per user and every document downstream is built from
what it produces; a thin profile costs more than the call. Roughly $1–3 per user with the document
prefix cached. Everything in the backend stays on `claude-haiku-4-5`, matching the reference's
`cv_model=haiku`.

> The thinking parameter is model-specific and this has already cost one debugging cycle:
> Haiku 4.5 requires `{"type": "enabled", "budget_tokens": N}`; Opus 5 **rejects** `budget_tokens`
> with a 400 and thinks by default. Structured output also rejects `maxItems`, `maxLength`,
> `minimum`/`maximum`, and any `additionalProperties` that is not `false`.

### Three questions the reference's agents do not ask

Found in Andras's changelog, never back-ported to mahoraga-jobs:

1. **Spelling convention.** `/setup` chose British English for his CV content and never recorded it
   as a rule, so the automated builds did not inherit it — `/setup-prompts` caught it later. The
   prompts read that field expecting it to exist.
2. **Salary target.** Skipped entirely: *"no target-ask line configured."* That is the 🎯 ask band
   the feed card shows.
3. **Work eligibility and sponsorship.** Where they may legally work, whether they need
   sponsorship, and where they *want* to work. Andras's setup **inferred** his home town from a
   summer job on his CV and flagged *"verify/correct this the first time a Hungary-based
   application comes up"* — a guess at the field that decides which country a recruiter thinks
   you are in.

**Current location is an onboarding field, not an agent question** (decided 2026-07-29). It is a
fact the user can simply state, it goes straight onto the CV, and a form is the right place to
collect a fact. The agents ask what can *shape a prompt* — a convention, a band, a rule about
eligibility — not what can be typed into a box.

## Eligibility filtering

Two halves, and the second is the one that saves the user time:

- **Asked at setup** — citizenship and rights to work, sponsorship needs, target regions.
- **Applied to every posting** — the reference has the hook in two places: `_REGION_LOCK_RE` in the
  feature extractor and a hard gate in the labelling rubric (*"remote but restricted to a region
  he cannot work → score 0–19 and stop"*). Both ship empty and are per user.

**Fails open.** A posting that is unclear stays and gets scored. A false gate silently hides jobs
somebody wanted, which is the worst failure this system has. "We do not offer sponsorship" and
"sponsorship available for exceptional candidates" are one word apart — the regex catches the
obvious cases for free, the rubric's gate reads the rest.

## Retraining is a notification, never a side effect

A scoring-relevant edit — `label.md`, `04-job-evaluation.md`, the token lists — **never** invalidates
labels on its own. It increments a counter and records what changed. Editing `context.md`, the base
CV or the writing prompts does not count: those change what is *written*, not what counts as a fit.

Once enough has accumulated, an in-app popup says what it would cost:

> Your rubric has changed thrice since your model was trained. Retraining re-scores 3,310 jobs —
> about $6 on your key, roughly 40 minutes.

Dismissing it hides it for that visit and **it returns on the next one**, because a model judging
against a stale rubric is worth being nagged about. Retraining — from the popup or from settings at
any time — resets the counter.

## Scoring: what the reference actually does

| Ported | Not ported, deliberately |
|---|---|
| `04-job-evaluation.md` as the framework, per user | **Cascade verify** (`verify_high`) — it is `false` in the reference's own config |
| **Shadow scoring**, sampled at 10% — Haiku grades the model alongside it and the disagreements feed the flywheel | |
| `scorer/fetch_desc.py` — applicants, easy-apply, reposted, workplace pill, **closed detection**, apply-URL resolution | |
| `refresh` before building, so no CV is built for a closed listing | |
| Opportunity score — age-decayed ranking | |
| `langfilter` | |

Shadow sampling is deterministic on `hash(job_id + user_id) % 10`, so a re-run does not re-spend,
and it counts against the daily quota. **10% is a guess until there is data**: collect the pairs,
then resample at 5/10/25/50/100% and measure how quickly a known drift is detected — the knee is
the answer. Say it is a guess until then.

Description fetch happens **per user on their own proxy**, after the free filters (dedup, language,
workplace, recency) — the reference fetches what it is about to score, not everything it found.
Overlapping fetches between users are accepted for now and are an optimisation later.

**Amended on delivery (2026-07-29): the fetch is split in two, by who pays.** Board and ATS pages
answer a datacenter IP, so those are described once in the shared harvest cycle — the corpus is
shared, so one fetch serves every account, and doing it per user would mean N users each fetching
the same Greenhouse page. LinkedIn keeps the per-user proxied path exactly as planned, because its
guest endpoint blocks datacenter addresses and the bandwidth must be metered to whoever caused it.

The shared half also runs **before embedding**, which the original ordering did not allow for: a
vector computed from a teaser is a vector of the advert, and every similarity read against it is
wrong in a way nothing downstream can detect.

## The feed, and the two buttons

The card is a line-for-line port of `watcher/feed.py` (already done). Two buttons, as the reference
has: **📄 Build CV** and **✉️ Build Cover**.

**A build is the positive label.** It spends the user's own credit, so nobody presses it on a job
they will not apply to — stronger evidence than a tap on "Applied", and it asks nothing further of
somebody who has already decided.

**A skip is not a negative.** A card scrolled past may have been missed rather than rejected, and
training on it teaches the model that inattention is dislike. Negatives come from **`/rate`**
instead, which asks about the jobs where a label teaches most: real description, no feedback yet,
**ordered by how close the score is to the threshold** — half of which are *below* it and never
reached the channel. Rating a delivered-only pool would teach the boundary from one side.

Bot commands: `/cv <link>`, `/cover <link>`, `/rate`. A pasted link is checked against the corpus,
fetched and added if absent, then scored for that user. Everything else lives in the web app.

## Applications page

Replaces `/stats`. Every job a CV or cover was built for, in a searchable, sortable, paginated
table: the job and its link, source, location, score, salary and your ask, posted age, applied date,
CV built at, cover built at. A stats strip above it — total, this week, by source, and the average
score of what you applied to versus what you were shown, which is the number that says whether the
model's idea of a good job matches yours.

Because a build is the positive label, this page is also a view of the training data. Say so on it.

**Failed builds appear as failed rows, with the reason.** Most will be credit exhaustion:

- **API key** — the provider returns a usable error, so warn when the balance is low and say plainly
  when a top-up is needed to keep going.
- **OAuth** — usage is not queryable, so surface the provider's own message and say it is almost
  certainly a usage limit rather than inventing a diagnosis.

Needs one migration: `cv_built_at` and `cover_built_at` timestamps, replacing the `cover_built`
boolean.

## Order of work

1. **Storage and import.** Per-user file table; generation reads files instead of `facts_md`.
   Import the reference instance's `CLAUDE.md`, seven skill files and `base_cv_dev.html` directly
   for the founder account — they are already his, no agent run needed. **CVs become correct here.**
2. **Chat and the agent loop.** Andras's second account runs `/setup` through it as the real test.
3. **Scoring and the feed.** `fetch_desc`, shadow sampling, refresh, opportunity score, `/rate`.
4. **Applications page** and the retrain popup.
5. **Delete the schema machinery** — `rubric.py`, `facts.py`, `docprompt.py`, `templates/cv.py`,
   the confirmation form, the generated types.

The live accounts keep delivering throughout; nothing is deleted until its replacement is proven.

## Decisions not to re-litigate

- **Copy the reference's prompts and agents verbatim.** Generalising during a port makes the diff
  unreviewable, and these are the most heavily tuned files in either repository.
- **Field-agnostic prompts stay identical for everyone.** `/setup-prompts` says it: *"never copy
  profile facts into a prompt — that duplicates the truth in two places and they drift."*
- **The user never sees the file machinery.** They complete a setup conversation; that it fills
  eight markdown files is an implementation detail.
- **Machine knobs are editable where onboarding is** — one place to change a fact about yourself.
- **Keep what the platform has that the reference does not**: the queue with per-user fairness, the
  shared corpus and its four dedup layers, the shared embedder, per-user proxy isolation, quotas,
  and migration-managed schema.
