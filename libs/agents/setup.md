# /setup - Profile Onboarding

You are running the onboarding setup for the AI Job Search framework. Your goal is to collect the user's professional information and populate all profile files so the `/apply` workflow works out of the box.

There are three paths into setup. Step 0 picks the right one; all three converge on Step 3 (file generation) and Step 4 (confirmation).

---

## Step 0: Welcome & Choose Path

If `$ARGUMENTS` contains `--section <name>`, skip directly to that section in Path C for an update-only flow. Do not run the path-selection prompt below.

Otherwise, before greeting the user, scan the `documents/` folder. Use Glob with `documents/**/*` and count files per subfolder (`cv/`, `linkedin/`, `diplomas/`, `references/`, `applications/`).

Then welcome the user with a single message that lists three paths. The wording changes based on what was found.

**If `documents/` has files** in one or more subfolders, lead with Path A:

> **Welcome to the AI Job Search setup!**
>
> I'll help you build your professional profile so Claude can evaluate job postings, tailor CVs, write cover letters, and prepare you for interviews.
>
> I see files in your `documents/` folder: [list per subfolder, e.g. "2 in cv/, 1 in linkedin/, 3 in references/"]. Three ways to start:
>
> **Path A: Read my documents folder** (recommended for what you have) - I'll read everything in `documents/`, cross-reference for consistency, and build your profile from real source materials. Idempotent and safe to re-run as you add more documents.
>
> **Path B: Single CV import** - Paste or @-mention a single CV/resume here. I'll extract it and ask follow-up questions for what's missing.
>
> **Path C: Interview mode** - I'll walk you through structured questions section by section.
>
> Which would you like?

**If `documents/` is empty or missing**, surface Path A as a "do this if you have materials" option:

> **Welcome to the AI Job Search setup!**
>
> I'll help you build your professional profile so Claude can evaluate job postings, tailor CVs, write cover letters, and prepare you for interviews.
>
> Three ways to start:
>
> **Path A: Documents folder** (best signal if you have several materials) - Drop your CV / LinkedIn export / diplomas / reference letters in the `documents/` folder, then say "go". I'll read everything and build your profile from it. See `documents/README.md` for the folder layout.
>
> **Path B: Single CV import** - Paste or @-mention a single CV/resume here. I'll extract it and ask follow-up questions for what's missing.
>
> **Path C: Interview mode** - I'll walk you through structured questions section by section. Good if you're starting from scratch.
>
> Which would you like?

Wait for the user's choice. If they pick A but the folder is still empty, tell them what to add (point at `documents/README.md`) and stop.

---

## Path A: Documents Folder

Reads structured documents in `documents/`, cross-references them for consistency, and merges extracted data into the seven profile skill files. Read-before-write and idempotent: changes already present will not be proposed again.

Follow these steps **exactly in order**.

### Step A1: Inventory

Use Glob with `documents/**/*` to scan the full tree. Print:

```
## Documents Found

**cv/**: [list files, or "(empty)"]
**linkedin/**: [list files, or "(empty)"]
**diplomas/**: [list files, or "(empty)"]
**references/**: [list files, or "(empty)"]
**applications/**: [list subfolders with their files, or "(empty)"]

I will read these and cross-reference before proposing any changes.
```

If every subfolder is empty, stop and tell the user to populate the folder. Point at `documents/README.md` for the layout.

### Step A2: Read Existing Skill Files

Read these in parallel before extracting anything. You must know what is already there to make the merge intelligent.

- `.claude/skills/job-application-assistant/01-candidate-profile.md`
- `.claude/skills/job-application-assistant/02-behavioral-profile.md`
- `.claude/skills/job-application-assistant/03-writing-style.md`
- `.claude/skills/job-application-assistant/04-job-evaluation.md`
- `.claude/skills/job-application-assistant/05-cv-templates.md`
- `.claude/skills/job-application-assistant/06-cover-letter-templates.md`
- `.claude/skills/job-application-assistant/07-interview-prep.md`

Hold this content in context throughout Path A. Do not re-read.

### Step A3: Parse Documents

Read each document found in Step A1. Process subfolders in this order: `cv/`, `linkedin/`, `diplomas/`, `references/`, `applications/`.

**`cv/` documents:** name, contact (email, phone, LinkedIn, GitHub), education (degree, institution, dates, thesis), work experience (title, company, dates, location, bullets), skills, publications, awards, profile/summary.

**`linkedin/` documents:** About/summary section (full text, used for behavioral inference), work experience, education, skills and endorsements, certifications, volunteer work, publications, recommendations received (full text). If multiple LinkedIn exports are present, use the most recently modified file.

**`diplomas/` documents:** official degree title and level, institution name (official spelling), graduation date, grade or distinction or GPA if visible.

**`references/` documents:** referee name, title, organization; full text of the letter (extract specific quotes); competency language used.

**`applications/<company>_<role>/` subfolders:**
- `job_posting.md`: role title, company, required skills, experience level, sector, role type
- `cover_letter.html` or `cover_letter.tex`: opening structure, body structure, bullet style, closing, recurring phrases
- `cv_draft.html` or `cv_draft.tex`: profile statement, section ordering, framing for this role type
- `outcome.md`: status (hired/rejected/no_response/interview_only), interview stages, notes

After reading, proceed to Step A4 without intermediate output. The user sees a complete picture in Step A6.

### Step A4: Cross-Reference Check

Before mapping anything to skill files, check for inconsistencies:

- Date mismatches between CV / LinkedIn / diploma
- Title mismatches across documents for the same role
- Education mismatches (degree name, graduation date)
- Employer name variations

If inconsistencies are found, present them as a numbered list and wait for the user to resolve each one before continuing:

```
## Cross-Reference Issues Found

These need to be resolved before I continue. For each one, tell me which version is correct.

1. **Role title mismatch - [COMPANY]:**
   CV says: "[TITLE_A]"
   LinkedIn says: "[TITLE_B]"
   Which is correct?

2. ...
```

If no inconsistencies, state "No cross-reference issues found." and continue.

### Step A5: Build Change Sets

For each skill file, compare extracted document content against the current file content from Step A2. Build two buckets.

**Additive changes:** entirely new content not in the skill file in any form. Examples: a certification not in `01-candidate-profile.md`, a new endorsement skill, a referee not yet listed, a new behavioral quote from a reference letter, a new award.

**Conflicting changes:** content that touches something already in a skill file but disagrees. Examples: a different date range for an existing job, a different job title for the same role, a different graduation date than what is recorded.

**Inference rules** (apply when populating from inferred sources):

- **`02-behavioral-profile.md`:** Source is LinkedIn About + recommendation letters. Extract recurring themes, adjectives, phrases about how the candidate works. Add only to "Strongest Behavioral Traits", "How [Candidate] Works Best", or "Management Style Preferences" sections. Do not overwrite existing scored assessments. Always label inferred additions: *[Inferred from LinkedIn About / Reference letter - review before relying on this]*
- **`03-writing-style.md`:** Source is `cover_letter.html`/`.tex` files. Extract recurring patterns. Add as observations under "## Patterns Observed in Past Applications". Do not modify existing rules. Only add if 2+ cover letters show a genuine pattern.
- **`04-job-evaluation.md`:** Source is `job_posting.md` + `outcome.md` pairs. If an application reached interview or offer: note role type and sector as a confirmed strong-fit signal. If 2+ applications repeat a no-response or rejection pattern: note it. Add findings under "## Calibration from Past Applications". Do not modify the existing scoring framework.
- **`05-cv-templates.md`:** Source is `cv_draft.html`/`.tex` files. Extract any profile statement that does not already appear in templates. Label with: *[Used for: <company>_<role>]*
- **`06-cover-letter-templates.md`:** Source is `cover_letter.html`/`.tex` files. Extract opening patterns, bullet structures, closing formulations. Add only what is structurally distinct from existing templates.
- **`07-interview-prep.md`:** Source is CV bullets, LinkedIn descriptions, reference letter quotes. Identify achievements not yet covered by an existing STAR example. Do NOT draft full STAR examples. Add stubs under "## STAR Candidates (Complete Manually)":

```markdown
### [Achievement title]
**Source:** [CV / LinkedIn / Reference letter - role/company]
**What happened:** [one sentence]
**Why it matters:** [interview question types this could answer]
**S/T/A/R stub:**
- Situation:
- Task:
- Action:
- Result:
```

### Step A6: Present and Confirm Changes

Present the full change set before writing anything.

**Additive changes** (single grouped list, organized by target file):

```
## Proposed Additive Changes

### 01-candidate-profile.md
- [ ] New certification: [title], [issuer], [date] - extracted from LinkedIn
- [ ] New reference: [name, title, company]
  Quote: "[relevant quote]"

### 02-behavioral-profile.md
- [ ] New behavioral observation [labeled as inference]: "[phrase]"

[and so on per file]
```

Then ask:

> **Apply all additive changes?** These add new content without touching anything already in the files.
> Reply **yes** to apply all, or list the numbers you want to skip.

Wait for the response. Apply only the confirmed items.

**Conflicting changes** (one at a time):

```
## Conflict 1 of [N]: Job title - [COMPANY]

**Current in 01-candidate-profile.md:**
[TITLE_A] - [COMPANY] ([START]-[END])

**Proposed (from LinkedIn export):**
[TITLE_B] - [COMPANY] ([START]-[END])

Options:
  [keep] Keep the existing text
  [replace] Replace with the version from the document
  [manual] I'll edit this myself - skip for now
```

Wait for the user's choice on each conflict. If no conflicts, state "No conflicting changes found." and skip this section.

### Step A7: Write Confirmed Changes and Fill Gaps

Apply the confirmed changes with the Edit tool. Make targeted edits only. Do not rewrite entire files. State which changes were applied per file. If a file has no confirmed changes, state "No changes made to [filename]."

Documents cover skills, experience, education, references, and behavioral signal. They do not cover everything `/apply` and the pipeline config need — and several of the gaps below are things a CV NEVER states but the system depends on. After the writes, ask follow-up questions for every gap:

- Career goals and target role types (priority order)
- What excites the user in their next role
- Deal-breakers and must-haves
- **Work eligibility** — which countries/regions they can work in without sponsorship, and where they would need it. A CV never says this, and it decides the CV Header Location Rule (see Path C Section 1 for why this matters).
- **Profile links for the CV header** — confirm the exact URLs found in the documents (LinkedIn, GitHub, portfolio, Scholar, ORCID, …), and ask for any the CV omits. Drop any they do not want shown.
- **Languages** — confirm the levels found, and ask separately which languages they could write an application in (this drives `JOB_LANGUAGES`).
- **Spelling convention** — British, American, or match-the-posting.
- **Workplace policy per region** — remote-only / hybrid-ok / would-commute, city by city where it matters.
- **Salary expectations per region** (optional — powers the feed's target-ask line).
- **Employment types** — full-time only, or contract / freelance / part-time too? Any outright dealbreakers (unpaid, equity-only)?
- Job search configuration (use the questions from Path C Section 9 below)

Use the fuller wording in Path C Sections 1, 7 and 9 for each of these — they explain to the user WHY each answer matters, which gets better answers.

Then proceed to Step 3 to populate the non-skill files (`CLAUDE.md`, the base HTML CV in `cv/`). Step 3 will detect that the seven skill files are already populated and skip those substeps.

---

## Path B: Single CV Import

If the user provides a single CV/resume:

1. Read the document thoroughly.
2. Extract all structured information: name, contact, education, experience, skills, publications, awards.
3. Present a summary of what was extracted.
4. Ask follow-up questions for gaps (behavioral profile, career goals, deal-breakers, salary expectations, references).
5. Proceed to Step 3 (file generation).

---

## Path C: Interview Mode

Walk through each section conversationally. Ask questions naturally, not as a form. Let the user answer in their own words and you'll structure the data.

### Section 1: Identity & Contact
Ask about:
- Full name
- Location (city, country)
- Phone, email
- **Profile links to put on the CV.** Ask which they have and get the EXACT URLs: LinkedIn,
  GitHub, portfolio / personal site, Google Scholar, ORCID, Behance / Dribbble, StackOverflow,
  or anything else their field expects. Only include links they would be happy for a recruiter
  to open — a stale, empty profile is worse than no link. These become the CV header, so any
  link they do not want is simply left out (never leave an empty placeholder in the header).
- **Languages.** For each: the language and their TRUE level. Ask two separate things — which
  they *speak*, and which they could **write a job application in**. The second list drives
  `JOB_LANGUAGES` (what gets scraped) and the CV-language rule; scraping a language they
  cannot apply in only creates noise. Never downgrade a genuine mother tongue to
  "Fluent"/"C2"/"Professional" — record it as Native.
- **Work eligibility (important — do not skip).** Which countries/regions can they legally work
  in without sponsorship (citizenship, permanent residency, existing visa)? And where would
  they need sponsorship? This is not idle data: it decides the **CV Header Location Rule** in
  `CLAUDE.md` (the header location follows where the JOB is, because a screener reads it as a
  work-eligibility signal — naming a city inside the employer's own hiring region removes the
  "needs sponsorship" doubt that silently kills applications). If they are eligible in more
  than one region, ask which city to present for each.
- **Spelling convention:** British, American, or another regional standard? Every CV and cover
  letter follows it consistently. If they have no preference, record "match the posting's
  region".
- Current employment status
- Family/commute constraints (if any)

### Section 2: Education
For each degree:
- Level (PhD, MSc, BSc, etc.), field, institution, years
- Thesis topic (if applicable)
- Key coursework or topics

Also ask about certifications (online courses, professional certs).

### Section 3: Professional Experience
For each role (most recent first):
- Job title, company, dates, location
- Key responsibilities (3-5 bullets)
- Key achievements or projects
- Technologies/tools used

Also ask about independent projects, freelance work, or side projects.

### Section 4: Technical Skills
- Programming languages + proficiency level
- ML/AI frameworks and tools
- Domain expertise
- Software tools and platforms
- Any other technical skills

### Section 5: Publications & Awards (optional)
- Peer-reviewed papers, conference presentations
- Hackathons, competitions, awards
- Skip if not applicable

### Section 6: Behavioral Profile (optional)
If they have a formal assessment (PI, DISC, Myers-Briggs, StrengthsFinder):
- Ask them to describe or share the results

If not, ask behavioral questions:
- "What work environments do you thrive in?"
- "What drains your energy at work?"
- "How do you prefer to work in teams?"
- "How do you make decisions, quickly or deliberately?"
- "What's your communication style?"
- Synthesize answers into a behavioral profile

### Section 7: Career Goals & Preferences
- Target roles and industries (in priority order)
- What excites you in work
- Deal-breakers and must-haves
- **Workplace policy, per region.** For each region they would work in, ask specifically:
  remote-only, hybrid acceptable, or would they genuinely go into an office there? Ask city by
  city where it matters — "would you commute to an office in <city>?" is a different question
  from "would you work in <country>?". This drives `ONSITE_LOCATIONS` and whether each search
  query filters on remote (`/setup-boards` uses it).
- **Salary expectations, per region.** The number they would actually accept, and the number
  they would ask for, quoted the way local employers quote it (monthly in some markets, annual
  in others). Tell them why it is worth answering: every job card in the feed shows what the
  posting pays next to their target ask, with a warning flag when the posting is under their
  floor. It is optional — skipping it just means no target line on the cards.
- **Employment types:** full-time only, or are contract / freelance / part-time also welcome?
  (This is a real scoring signal — it decides whether a whole class of postings is a match or
  a waste of attention.) Anything that is an outright dealbreaker (e.g. unpaid, equity-only)?
- What environments to avoid

### Section 8: References (optional)
For each reference:
- Name, title, company, email, phone
- Relationship to the user

### Section 9: Job Search Configuration
This section gathers the user's search targets (role titles, keywords, locations) — used to personalize `pipeline/config.yaml` in Step 4a. Use the information from Sections 1, 4, and 7 to build targeted queries.

Ask about:
- **Role titles to search for:** "What job titles should I search for? For example: Data Scientist, ML Engineer, Geophysicist." Collect 3-8 specific titles.
- **Key skills as search terms:** "Which of your skills are most likely to appear in job postings?" Pick 3-5 that are distinctive and searchable.
- **Target companies (optional):** "Are there specific companies you'd like to monitor for openings?"
- **Geographic scope:** "Which cities or regions should I search in? How far are you willing to commute?" Use this to define the location filter tiers (ideal, acceptable, borderline, too far).
- **Job portals:** "Which job sites or portals do you actually use (e.g. LinkedIn, a national job board, an industry-specific board, a recruiter platform)?" Use these to shape the pipeline's `config.yaml` searches (Step 4a) — LinkedIn/Indeed have adapters, and any other public board can be added as a Google `site:` search. No built-in regional bias.

**Important:** Also suggest role types the user may not have considered, based on their skill profile. For example:
- If they have strong Python + domain expertise: "Have you considered roles like 'Technical Consultant' or 'Solutions Engineer' in your domain?"
- If they have ML + a specific industry: "Companies in adjacent industries also hire for these skills. Should I include searches for [adjacent sector]?"
- If they have project management experience alongside technical skills: "Would you also want to search for 'Technical Project Manager' or 'Team Lead' roles?"

This proactive suggestion step helps users discover career paths they might not have considered.

---

## Step 3: Generate Profile Files

Once data collection is complete, generate or finish populating the following files. **For Path A**, the seven skill files are already populated by Step A7; check each before writing and skip if its content is no longer placeholder text.

### 1. Update `CLAUDE.md`
Read `CLAUDE.md` first. If the Candidate Profile section still contains template placeholder markers — any of
`[FILLED BY /setup]`, `[YOUR NAME]`, or other `[...]` bracket tokens (e.g. `[CITY, COUNTRY]`, `[PLACEHOLDER]`) —
it is an unpopulated fork template. Replace those markers with the user's actual information and keep the
structure, workflow, and verification checklist intact.

If it has **no** such placeholder markers and instead names a real person, it was already fully personalized for
someone else in a prior setup — do **not** silently leave it untouched (that would mean the new user inherits the
old user's name, contact details, and history in this always-loaded file) and do **not** silently overwrite it
either. Stop and tell the user:

> `CLAUDE.md` is already populated with another candidate's profile ([name found in it]). Continuing setup will
> rewrite the Candidate Profile section with your information. Reply **yes** to proceed, or run `/reset profile`
> first if you'd rather start from a clean slate.

Wait for confirmation before rewriting the Candidate Profile section (Identity, Education, Professional
Experience, Technical Skills, Certifications, Publications, Target Roles, Geographic Scope, Deal-breakers, CV
Header Location Rule). Leave the rest of `CLAUDE.md` (workflow steps, repo structure, verification checklist)
intact — those are framework rules, not personal data.

### 2. Populate `01-candidate-profile.md` *(Path B and C; skip if Path A populated it)*
Write the full candidate profile with structured sections: Identity, Education, Professional Experience, Independent Projects, Technical Skills, Publications, Awards, References.

### 3. Populate `02-behavioral-profile.md` *(Path B and C; skip if Path A populated it)*
Write the behavioral profile based on assessment results or synthesized answers.

### 4. Update `04-job-evaluation.md` *(Path B and C; skip if Path A populated it)*
Replace skill match areas with the user's actual skills:
- Strong match areas: [their primary skills]
- Moderate match areas: [their secondary skills]
- Weak match areas: [skills they lack]

Update career goals and motivation filters with their actual preferences.

### 5. Update `05-cv-templates.md` *(Path B and C; skip if Path A populated it)*
This file holds framework rules, not profile statement text — role-specific profile statements live directly in
the base HTML CV templates generated in step 7 below. The one thing to personalize here is the "CV Content
Judgement — Tailoring by Role-Type Lens" section: replace its generic domain-A/domain-B framing with the user's
actual concrete role directions (e.g. swap in their real categories in place of the dev/quant example), and spell
out what to cut and keep per domain the way a domain expert in their field would.

### 6. Update `07-interview-prep.md` *(Path B and C; skip if Path A populated it)*
Create STAR examples from their actual experience (at least 3-4 examples). Path A leaves STAR stubs under "## STAR Candidates (Complete Manually)" rather than full examples; if any stubs are present, mention them in Step 4 so the user knows to flesh them out.

### 7. Fill in the base HTML CV (`cv/base_cv.html`)
This framework uses plain HTML + Chrome headless for CVs, not LaTeX (see `05-cv-templates.md`). There is exactly
ONE base CV — `cv/base_cv.html` — and it is already in the repo as a placeholder scaffold. Do NOT create
per-role-type variants: the angle, emphasis and wording are decided per posting at tailoring time (the pipeline
and `/apply` both do this), so a second template would only drift out of sync. Write the user's real profile
into the existing file, in place.

**Structure is canonical — the user's own CV never overrides it.** Keep the scaffold's `<style>` block, `@page`
rule, font-face paths, and class names byte-for-byte (the automated pipeline restores exactly this CSS on every
build and relies on those class names). The section order is the recruiter-standard single-column layout —
header (name, contact line) → profile paragraph → core competencies → experience → education →
certifications/languages — which is also what ATS parsers read most reliably. If the user imported their own
CV, treat it as a **content source only**: extract the facts, never the layout, styling, section order, photo,
multi-column arrangement, or graphics. If they ask to keep their own design, explain the trade-off once (this
structure is tuned for the 6-second scan, ATS parsing, and the pipeline's automated compile checks) and follow
their decision for the interactive workflow — but note that automated pipeline builds always restore the
canonical layout regardless.

**Content comes from EVERYTHING collected, not just their CV.** Merge every source gathered during setup — the
documents folder, the pasted CV, and every answer given in this chat (projects, numbers, methodologies their CV
never mentioned) — so the base CV is richer than the one the user walked in with. Because this single template
feeds every application, it should hold the FULL set of relevant experience: per-job tailoring cuts down from
it, so anything missing here can never appear on any CV.

The app's goal applies to the base CV exactly as to tailored ones: every experience bullet must already follow
the bullet formula (STRONG VERB + SPECIFIC ACHIEVEMENT + QUANTIFIED IMPACT — see CLAUDE.md), entry titles must
follow CLAUDE.md's "Experience Entry Title Convention", the profile paragraph must pass the 6-second test, and
no task-description bullets ("Responsible for…") may survive the import verbatim — rewrite them from the
underlying facts. If the user targets more than one role direction, that lives in CLAUDE.md's "CV Content
Judgement" per-angle rules (which experience leads for which role type, what to cut), NOT in a second template.

**Verify before moving on:** compile the template with `cv/compile.ps1` and check it renders at exactly 2 pages
(or 1 clean page for a short history), fonts load (Lato, not a fallback), and no Chrome header/footer overlays
appear. A base CV that fails these checks fails on every tailored CV built from it.

### 8. Start the user's changelog (`docs/CHANGELOG.md`)

This repo ships without a changelog on purpose: the upstream project's development history is
not the user's, and a stale inherited history is worse than none. Create a fresh one for THEM.

It is gitignored, so it is theirs alone — it never conflicts when they pull upstream changes.

Write `docs/CHANGELOG.md` with exactly this shape (fill in today's real date):

```markdown
# Changelog — my instance

Why this file exists: `docs/PROJECT_STATE.md` says what the system IS right now; this says
WHY it got that way. Whenever you change something non-trivial — a new source, a filter or
scoring tweak, a prompt edit, a config decision you might second-guess later — add an entry
here: **what** changed, **where** it lives, and **why**. Newest first.

Months from now this is the difference between "why on earth is that set to 72?" and a
one-line answer. It is gitignored, so it stays yours.

---

## <YYYY-MM-DD> — Initial setup

- **Profile:** built from [documents / interview] via `/setup`.
- **Targets:** [their target roles, in priority order].
- **Regions / workplace:** [the policy they gave].
- **Base CV:** `cv/base_cv.html` populated; [note any deliberate choice, e.g. links included].
- **Next:** `/setup-boards` (search config), then `/setup-companies` + `/setup-scorer`.
```

Fill the bullets from what you actually collected in this run — that first entry is the record
of the decisions behind their setup. Tell the user the file exists and what it is for.

The user's job search targets (roles, keywords, locations) are configured in
`pipeline/config.yaml` in Step 4a below — that is the single source of truth for what gets
scraped.

---

## Step 4: Set up the Automated Stack (the product)

The automated stack is the main way to use this framework: a **24/7 watcher** streams fresh, high-fit jobs to a Telegram channel minutes after they are posted (scored for free by a local model trained on **the user's own profile**), and the pipeline + bot build tailored CVs on demand — on the user's machine or a VPS, driven from Telegram. This step gets it configured. (The interactive `/apply` workflow still works without any of this.)

> **If this /setup run was started by `/setup-all`, stop here** — the orchestrator's stages 2–6 replace this
> entire step with a fuller guided flow. Report Step 3 complete and return.

**State the requirements up front and wait for confirmation:**

> The automated stack needs:
> - a **Claude subscription** (Max recommended) — it labels jobs and writes the CVs/covers on your account (required),
> - **Docker** — everything runs in containers, on your own machine or a server (required),
> - a **residential proxy** (~$1/GB) — LinkedIn blocks datacenter IPs; without one you lose LinkedIn but keep all the direct company boards (recommended),
> - a **VPS** (4–8 GB RAM) — only if you want the 24/7 radar while your machine is off (optional).
>
> All-in it lands around **$25–30 / month**, and setup takes about **1–2 hours end-to-end** (most of it profile building and the first model training — see `docs/GETTING_STARTED.md`). The payoff: fresh high-fit jobs streamed to your phone minutes after posting, each with a tailored 2-page CV one tap away, scored for free by a model that learns your taste. Want to set it up now, or just configure it for later?

If they don't have the infra yet, still do **Step 4a** (config is just files) and hand them the checklist for when they do.

### Step 4a: Personalize the search config
Run the **`/setup-boards`** flow (`.claude/commands/setup-boards.md`) — it owns this interview end-to-end:
role keywords, regions with per-region remote/hybrid/on-site policy, seniority blocks, the field gate,
languages, and it writes `searches` (LinkedIn/Indeed/Google, hiring.cafe, the direct-ATS keyword/exclude/location
anchors, Tier-2 boards), `phase1_filters`, and the `.env` filter variables — then stamps the `# personalized:`
marker. Do not duplicate a shorter version of that interview here. (`/setup --section search` re-runs this
whenever targets evolve.)

### Step 4b: Generate `.env`
Read `.env.example` and create `.env` (gitignored) with the user's values, collected conversationally —
`docs/GETTING_STARTED.md` Steps 2–4 is the user-facing walkthrough for where each value comes from. Do NOT echo
secret values back; just confirm each is set:
- `CLAUDE_CODE_OAUTH_TOKEN` — run `claude setup-token` on the machine that runs Docker.
- `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` — create a bot via @BotFather; get the chat id from the
  `getUpdates` URL method (GETTING_STARTED Step 2) or `python -m pipeline.get_chat_id` (only while the
  notifier is stopped — Telegram allows one getUpdates consumer at a time).
- `WATCHER_CHAT_ID` — the separate feed channel's id (GETTING_STARTED Step 3); blank = watcher scores +
  logs without notifying.
- `PROXY_URL` — their residential proxy endpoint (blank = no LinkedIn, direct boards still work).
- `CANDIDATE_NAME` — their full name; output PDFs are named from it (e.g. `Jane_Doe_CV.pdf`).
- Leave `CLAUDE_MODEL` / `CV_MODEL` at defaults, and `SCORING_ENGINE=claude` for now — `/setup-scorer`
  flips it to `model` once their local model is trained.

### Step 4c: Deploy + first run (hand them this checklist)
These run on the Docker machine (their own box, or a VPS via `docs/VPS_SETUP.md`):
1. Clone the repo there, copy `.env` over (scp for a VPS).
2. `docker compose up -d --build scorer-service notifier watcher` — the always-on services. Tell them the
   watcher will log `scorer service not ready` until step 4 — expected, not a bug.
3. Fill the link pool: `docker compose run --rm pipeline` (full scrape; with `SCORING_ENGINE=claude` it also
   scores the top links).
4. **Train their model:** run the **`/setup-scorer`** flow (`.claude/commands/setup-scorer.md`) — rubric
   personalization, `scorer.label --n 300` batches to ~800–1,000 labels, train, serve, flip
   `SCORING_ENGINE=model`. The watcher comes alive at this moment.
5. From Telegram: watch the feed channel; tap 📄 Build CV, or `/build_cvs 5`, `/getcv <company>`,
   `/cover <company>`.
6. Optional: a cron running `docker compose run --rm pipeline python -m pipeline.orchestrator --score-only`
   twice a day for bulk scoring — the watcher already covers fresh detection continuously.
Point them at `docs/COMMANDS.md` for the full command reference and `docs/GETTING_STARTED.md` → "Keeping it
sharp" for the weekly maintenance loop.

---

## Step 5: Confirm & Next Steps

Present a summary:

> **Setup complete!** Here's what was generated:
>
> - `CLAUDE.md` - Your full candidate profile
> - `.claude/skills/job-application-assistant/01-candidate-profile.md` - Structured profile
> - `.claude/skills/job-application-assistant/02-behavioral-profile.md` - Behavioral assessment
> - `.claude/skills/job-application-assistant/04-job-evaluation.md` - Personalized evaluation framework
> - `.claude/skills/job-application-assistant/05-cv-templates.md` - CV templates with your profile statements
> - `.claude/skills/job-application-assistant/07-interview-prep.md` - STAR examples from your experience
> - `cv/base_cv.html` - Your base HTML CV (one template; every application is tailored from it)
> - `docs/CHANGELOG.md` - Your own change log (gitignored — record why you change things)
> - `pipeline/config.yaml` - Your job-search config (roles, keywords, locations) for the pipeline
>
> **Next — the automated stack (recommended):**
> - Continue with `/setup-all` (it resumes at the next incomplete stage) or follow `docs/GETTING_STARTED.md`:
>   Telegram bot + watcher channel, `docker compose up`, seed companies (`/setup-companies`), train your
>   model (`/setup-scorer`) — then fresh high-fit jobs stream to your phone 24/7 with one-tap CVs.
> - It scores jobs free with a model that learns *your* taste — every `/applied`, `/skip`, and 👍 trains it further.
>
> **Or the interactive workflow (no Docker needed):**
> - Run `/apply` with a posting URL for a single tailored CV (automated discovery is handled by the stack).
> - Run `/setup --section search` later to update your targets as priorities evolve.

If Path A left any STAR stubs in `07-interview-prep.md`, also note:

> Path A flagged [N] STAR candidate stubs in `07-interview-prep.md` that need your situation/task/action/result details before you use them in interviews.

---

## Design Principles

- Three onboarding paths converge on the same skill files. Step 0 picks the right path based on what's in `documents/`. Steps 3 and 4 are shared.
- Path A is read-before-write and idempotent. Re-running it as documents are added does not duplicate or overwrite existing content; conflicts are surfaced for explicit resolution.
- Path A labels inferred behavioral or style additions so the user can review them critically before relying on them.
- Each section in Path C is a natural conversation, not a form. The user can skip optional sections.
- Synthesize answers into structured formats (the user does not need to know markdown or LaTeX).
- Can be re-run with `--section <name>` to update specific sections (e.g., `/setup --section search` to reconfigure job search queries without re-doing the full profile).
- Section 9 (search) in Path C, and the equivalent follow-up questions in Path A, proactively suggest role types the user may not have considered.
- At the end, suggest running `/apply` with a test job posting.
