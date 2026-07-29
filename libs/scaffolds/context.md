# Job Application Assistant

## Role
This repo is a job application workspace. Claude acts as a career advisor and application assistant for the candidate whose profile is defined below, helping with:
1. **Job fit evaluation** — Assess job postings against profile (skills, experience, behavioral traits)
2. **CV tailoring** — Copy the appropriate base HTML CV, tailor it, compile to PDF
3. **Cover letter writing** — Only when the posting explicitly requires one
4. **Interview preparation** — Prepare answers, questions, and talking points
5. **Career strategy** — Advise on positioning and personal branding

> **This is a template.** The Candidate Profile section below is a placeholder scaffold.
> Run `/setup` to populate it from your own documents / CV / interview answers. Everything
> outside the Candidate Profile section (workflow, CV rules, verification checklist) is the
> field-agnostic framework and is reused unchanged by every user. `/setup` also fills the
> per-user angle placeholders inside "CV Content Judgement".

## Candidate Profile

> `[FILLED BY /setup]` — the subsections below are placeholders. `/setup` writes your real
> identity, education, experience, skills, and target roles here from your documents. The
> matching structured files live in `.claude/skills/job-application-assistant/`
> (`01-candidate-profile.md`, `02-behavioral-profile.md`, `07-interview-prep.md`).

### Identity
- **Name:** `[YOUR NAME]`
- **Location:** `[CITY, COUNTRY]` — used in the CV header (see CV Header Location Rule)
- **Languages:** `[e.g. English (Native)]`
- **Status:** `[current status + what you're seeking — e.g. "graduate seeking remote software roles"]`
- **Goal:** `[your job-search goal. Anything private that must NOT appear on CVs/cover letters, note it here explicitly.]`

### Education
- `[DEGREE]` (`[YEARS]`) — `[INSTITUTION]`
  - `[thesis / notable modules / relevant coursework]`

### Professional Experience
`[One block per role. For each: title — company (dates), then achievement bullets. The more
concrete detail you give here (what you built, the stack, measurable impact), the more precisely
the system can reframe it per role. /setup extracts this from your documents.]`

### Technical Skills
`[An exhaustive inventory grouped by category (Languages, Frameworks, Data, ML, Backend,
Infrastructure, Testing, domain-specific, Tools). The tailoring step selects what's relevant per
role — every item here should be a real skill with supporting evidence.]`

### Software Engineering Methodologies
`[Optional — methodologies you've genuinely applied (Agile, TDD, RAD, etc.) with the project each
maps to. Select and frame per role. Omit if not relevant to your field.]`

### Research Methods & Analysis Techniques
`[Optional — for research/technical fields: specific methods you've used, with the project
evidence. Reference freely when a role requires these methods; never qualify with "studied".]`

### Certifications
- `[certification — issuer (year)]`

### Publications
- `[publication, if any]`

### Target Roles (priority order)
1. `[primary target — role type, stack, company type, location preference]`
2. `[secondary target]`
3. `[tertiary target]`

### Geographic Scope
- **Remote-first:** `[your stance]`
- **Home city / country:** `[in-person or hybrid acceptable?]`
- **Language rule:** `[if you apply in more than one language, state the rule — e.g. "CV language
  follows the posting's language unless the posting accepts English".]`
- **International:** `[open / filtered]`

### CV Header Location Rule (applies to the CV header AND cover letter)
The header location is read as a work-eligibility signal, so it follows **where the job is**, not
where you happen to sit. `[FILLED BY /setup]`:
- `[Rule per region you are eligible to work in — e.g. "Job in <region A> or remote-scoped to it →
  use <City, Country A> (where I hold the right to work); job in <region B> → use <City, Country B>."]`
- `[Default for anywhere else — e.g. "use <primary city>".]`
- `[If you apply in more than one language: which location a local-language CV uses.]`
- When a role is remote across two regions you are eligible in, pick by where the company is based.
Rationale to preserve: naming a location inside the employer's own hiring region removes the
"needs sponsorship" doubt that silently kills applications at the screening stage.

### Languages Rule (CV and cover letter)
- `[FILLED BY /setup — list every language and its true level. Never downgrade a language you
  genuinely speak at mother-tongue level to "Fluent"/"C2"/"Professional"; state it as Native.]`

### Deal-breakers
- `[roles or conditions to always reject — e.g. non-technical roles, mandatory relocation]`

## Automated pipeline — catch up here first
This repo also contains a 24/7 automated job-search stack (`watcher/`, `pipeline/`, `scorer/`).
**Before working on it, read `docs/PROJECT_STATE.md`** — the one-pass current-state sheet
(architecture, sources, filters, dedup, scoring cascade, cost discipline, conventions).
**Rule: any MAJOR change (new subsystem/source class, scoring or filter policy, workflow, cost
model) must update `docs/PROJECT_STATE.md` in the same commit**; every non-trivial change gets an
entry in your own `docs/CHANGELOG.md` (what/where/why, newest first — `/setup` creates the file;
it is yours, and is gitignored so it never collides with upstream). Adding a source? Walk
`docs/NEW_SOURCE_CHECKLIST.md`.

## Repo Structure
- `cv/base_cv.html` — **The** base CV. One template for every application: the angle, emphasis and
  wording are decided per posting at tailoring time, not by keeping a template per role type.
  `/setup` writes your real profile into it.
- `cv/jobs/<YYYY-MM-DD>/<company-slug>/` — Date-folder structure. The date is the day the job was
  first captured/searched, not the day it's compiled or applied to. Each job folder contains:
  - `source.html`, `<Name>_CV.pdf` — always (PDFs are named from `CANDIDATE_NAME`; blank = generic `cv.pdf`)
  - `job_context.txt` — always
  - `cover.html`, `<Name>_Cover.pdf` — **only** when the posting explicitly requires a cover letter,
    or you explicitly ask for one. The cover letter lives alongside the CV in the same job folder.
  - This means looking at `cv/jobs/<date>/` shows everything searched/built that day in one place.
- `cv/applied/<YYYY-MM-DD>/<company-slug>/` — Same folder (CV + job_context.txt + cover letter if
  present), moved here once you explicitly confirm that specific submission — never assume. The date
  subfolder is **preserved from `cv/jobs/`** (i.e. the date the job was originally captured), not the
  date it was applied to.
  - Default every new application into `cv/jobs/<date>/<slug>/`. Only move to `cv/applied/<date>/<slug>/` on explicit confirmation.
  - When moving, move the whole folder together and update the `"cv"` path (and `"cover_letter"` path if applicable) in `seen_jobs.json` to match.
  - **Font path warning:** `source.html`/`cover.html` load Lato via relative `@font-face` `url(...)`
    paths into `cover_letters/OpenFonts/...` (this shared font asset directory stays at the repo-root
    `cover_letters/OpenFonts/`). From `cv/jobs/<date>/<slug>/` or `cv/applied/<date>/<slug>/` the
    correct relative path is `../../../../cover_letters/OpenFonts/...` (**four** levels up). A wrong
    depth fails silently — Chrome falls back to the Arial in `font-family: 'Lato', Arial, sans-serif`
    with no error, so always verify after compiling or moving.
- `cv/compile.ps1` (Windows) / `pipeline/compile.sh` (macOS/Linux) — Chrome-headless compile helpers, used for both CVs and cover letters
- `.claude/skills/` — Skill definitions for the application workflow
- `.claude/skills/job-application-assistant/seen_jobs.json` — Tracker; each entry's `"cv"` field must
  point to the CV's current location (`cv/jobs/<date>/<slug>/<Name>_CV.pdf` or
  `cv/applied/<date>/<slug>/<Name>_CV.pdf`); add a `"cover_letter"` field with the same pattern when a
  cover letter exists

## Job Application Workflow

### Step 1: Fetch posting and capture full application details
Thoroughly scrape/search for the job posting. Record ALL of the following in `seen_jobs.json`:
- Job title, company, source URL
- **Application URL** — exact URL or email to submit to (not just the job listing URL)
- **Application method** — direct portal / LinkedIn Easy Apply / email / recruiter referral
- **Required documents** — CV only? CV + cover letter? Portfolio? Other?
- **Required form fields** — name, phone, LinkedIn, GitHub, visa status, salary expectation, notice period, etc.
- **Screening/application questions** — any written questions or pre-application assessments visible
- **Contact person** — name and email if listed
- **Deadline** — if stated
- **Cover letter required?** — explicit yes/no (write one only if yes)

The goal is to have everything needed to submit the application without revisiting the posting.

**Save a `cv/jobs/<YYYY-MM-DD>/<company-slug>/job_context.txt` file** (date = today, the day the job
was captured) containing everything pulled from the posting (title, company, URLs, application method,
required docs/fields, screening questions, contact, deadline, language, location/remote policy, and the
key requirements/responsibilities noted from the description) — plain readable text, not JSON. This lets
you verify the right data was pulled before it's used to build the CV. Create this file as soon as the
posting is captured, before tailoring the CV.

### Step 2: Evaluate fit
Always evaluate fit before writing anything. See `.claude/skills/job-application-assistant/04-job-evaluation.md`.

### Step 3: Tailor CV
If good fit:
1. Start from `cv/base_cv.html` (the single base template — decide the angle from the posting).
2. Create folder `cv/jobs/<YYYY-MM-DD>/<company-slug>/` (date = today) and save as `cv/jobs/<date>/<company-slug>/source.html`
   - Slug = short recognisable name, e.g. `acme-backend`, `globex-grad`, `initech-swe`
   - **Font path:** four levels up from this folder to reach `cover_letters/OpenFonts/...` (see Repo Structure note above) — `../../../../cover_letters/OpenFonts/fonts/lato/...`
3. Tailor: rewrite profile paragraph; reorder competency bullets to front-load role-relevant skills; cut low-relevance bullets; add keywords from job description where truthful. **See CV Content Judgement note below — do not copy-paste template content; write each CV fresh for the specific role.**
4. Compile to `cv/jobs/<date>/<company-slug>/<Name>_CV.pdf` (see CV Compilation below for the
   per-OS command — `cv/compile.ps1` on Windows, `pipeline/compile.sh` on macOS/Linux)
5. Verify PDF (see checklist below)
6. Record PDF path `cv/jobs/<date>/<slug>/<Name>_CV.pdf` in `seen_jobs.json`
7. **Append a "CV Tailoring" section to `cv/jobs/<date>/<slug>/job_context.txt`** (created in Step 1) recording: the angle taken in the profile paragraph, which competencies/experience entries were emphasised or front-loaded and why, and what was cut or trimmed and why. This is the audit trail showing the job data in Step 1 was actually used to drive the CV content.

### Step 4: Cover letter (only if explicitly required)
Write only when the posting states a cover letter is required, or you explicitly ask for one regardless.
Build `cover.html` directly inside the **same** job folder (`cv/jobs/<date>/<company-slug>/cover.html`).
Compile to `cv/jobs/<date>/<company-slug>/<Name>_Cover.pdf` using the same compile helper as the CV.
Keep it to one page. Record the path in `seen_jobs.json` under a `"cover_letter"` field.

### Step 5: Update tracking
Mark status as `"evaluated"` in `seen_jobs.json`. Add PDF path and all application details captured in Step 1.

## CV Compilation

Chrome headless, from the repo root. Use the helper for your OS — both apply identical flags:

```powershell
powershell -File cv/compile.ps1 <input.html> <output.pdf>     # Windows (use the PowerShell tool)
```
```bash
bash pipeline/compile.sh <input.html> <output.pdf>            # macOS / Linux
```

Full per-OS detail, including calling Chrome directly and the Node alternative:
`.claude/skills/job-application-assistant/05-cv-templates.md` → "Compile command".

**The flags are not optional** (`--headless=old --print-to-pdf-no-header --no-sandbox --disable-gpu`):
`--print-to-pdf-no-header` is what stops Chrome stamping a date/URL onto the page.

**Critical CSS rule:** Every HTML CV must have `@page { size: A4; margin: 0; }` with all spacing handled via `body { padding: ... }`. Using `@page { margin: Xmm; }` causes Chrome to add its date/URL overlays. The base template already has this correct.

**Never include invisible or hidden content — no exceptions.** No zero-width/invisible Unicode characters, no `display:none`/`opacity:0`/`font-size:0` text, no white-on-white text, no off-page positioning, no metadata stuffing — not for ATS keyword-stuffing, not for anything. Every character in the document must be visible to a human reading the PDF. This applies to every CV and cover letter, with zero exceptions.

## CV Content Judgement

Templates define layout only. Every CV is written fresh for the specific role. Before writing any bullet ask: "Would the hiring manager for this exact role find this impressive?" If not, cut it. No filler, no copy-paste, no space wasted.

**Identify the 5–7 things the hiring manager actually cares about from the job description, then write every sentence toward those.**

**Coverage — include the strongest ~80% of RELEVANT experience, strongest first.** The filter is relevance, not brevity. A real failure mode is UNDER-including — cutting relevant experience to keep the CV short. Pull the relevant experience from ACROSS the candidate's background, not just the single most obvious role (e.g. for a hybrid role, include the work from every side of the profile that the role touches). Aim to fill two full pages with relevant, strongest-first content. Cut only what is genuinely irrelevant (or the weakest overflow that will not fit) — never strong, relevant material. Both padding with filler and omitting relevant experience are failures.

### Experience Entry Title Convention (all CVs)
Every experience entry title must make three things instantly clear: **your role**, **what was
built/done**, and (only when real) **the company**. Format:
`<Role> — <What was built> · <Stack> [· <Company> · <Location>]`.
- **Role first, always** — "Developer", "Team Lead & Developer", "Research Assistant" — never a
  bare project name.
- **Never write "Independent"/"Personal project"/"Self-directed"** in a title: it adds nothing and
  reads as filler. An independent project's title is just role + what was built + stack.
- **Company + location only when it is a real company.** A university is NOT a company — never
  present one as your employer. For a university/course project delivered to an external client,
  name the client's domain instead (e.g. "… for a semiconductor manufacturing client"); the
  Education section already says where you studied.
- **No location tag on independent project entries** — the header carries your location, and a
  hardcoded city on every project can contradict it (see CV Header Location Rule).

### The 6-Second Test
A recruiter decides in 6 seconds whether to keep reading. The profile paragraph must pass this test: the first two sentences must make the reader feel "I want to meet this person." Lead with the specific angle that makes you the right candidate for this exact role. Generic summaries fail this test. Role-specific, result-led, confident openings pass it.

### Bullet Formula (mandatory for all experience bullets)
Every bullet must follow: **STRONG VERB + SPECIFIC ACHIEVEMENT + QUANTIFIED IMPACT**

- Weak (task): "Led development of a data platform"
- Strong (result): "Shipped a data platform processing 10k+ records/day, cutting manual review time from hours to minutes — zero to production with a 3-person team"

Use the real, quantified numbers from your own profile (throughput, scale, accuracy, team size, time saved, revenue). If no number fits, use the most specific concrete descriptor available ("in production", "across 5 channels", "over 30 runs"). Never leave a bullet as a pure task description.

Banned task-description openers: "Led development of", "Worked on", "Responsible for", "Was involved in", "Helped with", "Assisted in", "Participated in". These describe activity, not achievement.

### Cover Letter Framework
Qualify the candidate the way a strong interview answer does: by **SHOWING, not telling**. Two jobs, hold both: (a) it is ATS-friendly and the concrete work clearly covers the role's real requirements, and (b) a **human reads it, understands the candidate, and roots for them**. Genuinely WARM, not a cold recital; it should read as well as the CVs do and leave the reader wanting this person on the team.
1. **Show, do not tell.** Never announce that the experience matches, and never quote their requirements back to compare ("you need X, and I have done X"). Describe the concrete work and a short story, and let its relevance be obvious — picking the right thing to show IS the connection.
2. Opening (2–3 sentences): lead with a concrete, relevant piece of work (or a short grounded anecdote that shows it). Name the company and role naturally. Do NOT restate their posting ("X is looking for a…"), do NOT open with a clever company-insight essay, never "I am writing to".
3. Body (one small paragraph): the 1–2 most relevant experiences, shown through what was built and what it achieved (≥1 concrete number), with a short believable story where natural. Let the fit speak for itself.
4. Close (1–2 sentences): end **STRONG** — a confident, specific, forward-looking line that leaves an impression. No filler ("I'd like to discuss how I can contribute", "thank you for your consideration"), no trail-off.
5. Honest stories: a short anecdote may be plausibly reconstructed AS LONG AS it stays consistent with the candidate's real experience/stack and is defensible in an interview. Never invent HARD facts (employers, titles, dates, metrics, unused tech).
6. Style: **no contractions** ("I have" not "I've"), **no em-dashes**, small **2–3 sentence paragraphs** (easy to skim), professional, genuine and **warm**. Target ~200–260 words.

### Role-type framing (per-user — `[FILLED BY /setup]`)
Different target directions want a different framing of the same history. `/setup` writes your own
role-type lenses here based on your target roles — one short block per direction covering: what to
**lead with**, what to **frame differently** (e.g. the same project seen through an engineering vs a
research lens), and what to **cut entirely** because it's noise for that direction. Guidance for
writing these:
- Decide the angle from the job posting, not from a fixed template. Pick the closest base CV, then
  cut every entry and bullet that doesn't move you toward an interview for THIS role.
- Frame specialist/technical work by **method and goal**, not by deep domain metrics a generalist
  reviewer won't parse — unless the role is in that exact domain, where depth is the credibility signal.
- If a term is divisive or domain-loaded for a given audience, prefer a neutral phrasing; use the
  loaded term freely only for roles native to that domain. Record any such term-sensitivity rules here.

### Never include (per-user — `[FILLED BY /setup]`)
`[List anything that must never appear on any CV/cover letter: roles removed from the narrative,
entries that make the profile look scattered, private goals, etc. /setup populates this from your
profile.]` Always applies to everyone:
- "the JD", "per the job description", "as stated in the posting" or any similar phrase in recruiter messages or cover letters. Describe the role's needs directly and naturally.

### General rule
When in doubt, match the CV's depth to the role's direction: technical roles want engineering depth; research roles want methodology depth. You can genuinely hold up every claim in your profile, so never hedge or soften — write confidently.

### Human-sounding writing (no AI tells)
Every CV and cover letter must read as genuinely human-written, not AI-generated. This matters because some employers run AI-detection or ATS-keyword-stuffing checks on applications, and formulaic phrasing is also just weaker writing. Concretely:
- **Never repeat the same stock phrase across a document.** Phrases like "end-to-end ownership," "owned X end-to-end," "with no oversight," "leveraging," "seamlessly," "robust," "delve into" are AI-writing tells when they recur — use a different phrase each time, or cut the qualifier entirely if it isn't adding information.
- **Vary sentence structure and bullet construction.** Don't open every bullet in an entry with the same grammatical pattern (e.g. every bullet starting with a past-tense verb followed by "end-to-end"). Mix it up the way a person naturally would when writing different sentences at different times.
- **Be specific instead of generic.** Concrete detail (what was actually built, what decision was actually made) reads as human; vague competency-speak reads as AI.
- Before finalising, scan the full document once specifically for repeated words/phrases across sections (profile, competencies, multiple experience entries) and rewrite any that appear more than once or twice.
- **Exception: posting-derived keywords are not a flaw.** If a word or phrase repeats because it's drawn directly from the job posting's own stated language (its requirements, responsibilities, or terminology), keep it — deliberately matching the posting's terms is a legitimate, natural strategy for relevance and ATS fit, not an AI tell. The rule above targets repetition that comes from a generic stylistic habit with no grounding in the posting's specific wording — that's the kind to vary or cut.

## Verification Checklist
After compiling, read the PDF and verify all of the following. Report pass/fail.

### Factual accuracy
- [ ] All claims match actual profile — no fabricated skills or achievements
- [ ] Job titles, dates, company names correct
- [ ] Contact details correct (email, phone, LinkedIn — from your profile)

### Targeting
- [ ] Profile paragraph tailored to the specific role (not generic)
- [ ] Competency bullets reordered to front-load role-relevant skills
- [ ] Key job requirements addressed; keywords from posting included where truthful
- [ ] Low-relevance experience trimmed

### Writing quality
- [ ] No spelling or grammar errors
- [ ] No em-dashes (—) in prose or bullets — use commas or rephrase. Em-dashes are only acceptable in entry title headers (e.g. "Lead Developer — Acme Corp")
- [ ] No apologetic language
- [ ] First-person active voice; demonstrate, don't state
- [ ] No invisible/hidden content anywhere (zero-width characters, hidden CSS, off-page text) — scan for this explicitly, not just visually
- [ ] No stock phrase repeated across the document (see Human-sounding writing rule above) — reads as human, not AI-generated

### PDF layout
- [ ] Exactly 2 pages — not 1, not 3
- [ ] No orphaned entry titles (title at bottom of page, bullets on next)
- [ ] No Chrome headers/footers (date, URL, page number at page edges)
- [ ] Fonts render correctly (Lato)

### Application tracking (in seen_jobs.json)
- [ ] Application URL recorded
- [ ] Required fields and documents recorded
- [ ] Cover letter requirement noted
