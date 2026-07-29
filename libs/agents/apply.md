# /apply - Drafter-Reviewer Job Application Workflow

You are orchestrating a two-agent job application workflow. The job posting is provided below as `$ARGUMENTS` (either a URL or pasted text).

Follow these steps **exactly in order**. Do not skip steps.

**Token-efficiency rules for this workflow:**
- Never re-Read a file whose contents are already in your context from an earlier step. If you read it in Step 1, it is still available in Step 2.
- When dispatching the reviewer agent, pass draft content **inline in the agent prompt** rather than asking the agent to Read files you already have in memory.
- Run the full verification checklist exactly once, at the end (Step 7). The reviewer focuses on content critique, not verification.
- Step 6 (compile and inspect PDFs) is mandatory and non-skippable. CV/cover-letter HTML that looks fine in the source often produces broken PDFs once Chrome paginates it (orphaned entry titles, content spilling to an extra page, a font silently falling back to Arial because a relative path is wrong).

---

## Step 0: Parse Input and Capture Full Application Details

- If `$ARGUMENTS` looks like a URL, use `WebFetch` to retrieve the job posting content.
- If it is pasted text, use it directly.
- Extract: **company name**, **role title**, **department** (if mentioned), **location**, **remote/hybrid/onsite policy**, and **language** of the posting.
- Also extract everything needed to submit the application without revisiting the posting: **application URL** (exact submission link/email, not just the listing URL), **application method** (direct portal / easy-apply / email / recruiter), **required documents**, **required form fields**, **screening/application questions**, **contact person**, **deadline**, and whether a **cover letter is explicitly required**.
- Derive a `company-slug` (short, recognizable, lowercase-hyphenated) and today's date in `YYYY-MM-DD`.
- Create `cv/jobs/<date>/<company-slug>/job_context.txt` now, before drafting anything, containing all of the above in plain readable text (not JSON) — see `CLAUDE.md` Repo Structure / Step 1 for the exact convention and folder layout this repo uses. This lets the candidate verify the right data was pulled before it drives the CV.

---

## Step 1: DRAFTER - Evaluate Fit

Read the evaluation framework:
- `.claude/skills/job-application-assistant/04-job-evaluation.md`
- `.claude/skills/job-application-assistant/01-candidate-profile.md`

Using the framework from `04-job-evaluation.md`, evaluate the job posting against the candidate's profile. If a salary lookup tool is configured (`tools/salary_lookup.py`), run it and include the result; if it errors or no data file exists, skip the salary benchmark rather than guessing.

Present the evaluation to the user with:

1. **Skills match** — which required/preferred skills match vs. gaps
2. **Experience match** — how work history maps to the role
3. **Behavioral/culture match** — how behavioral profile fits the role/company culture
4. **Salary benchmark** — if available
5. **Overall fit score** and recommendation (strong fit / moderate fit / weak fit)
6. **Which base CV template fits this role** — per the candidate's configured base templates (see `05-cv-templates.md`); if the role type doesn't cleanly match an existing template, say so and ask the user which to use or whether to create a new one.

After presenting the evaluation, ask the user:
> "Should I proceed with drafting the CV and cover letter for this role?"

**If the user says no, stop here** (still keep `job_context.txt` — it's useful even for jobs not pursued). If yes, continue to Step 2.

---

## Step 2: DRAFTER - Draft CV + Cover Letter

You already have `01-candidate-profile.md` and `04-job-evaluation.md` in context from Step 1. **Do not re-read them.**

Read only the reference files you do not yet have:
- `.claude/skills/job-application-assistant/03-writing-style.md`
- `.claude/skills/job-application-assistant/05-cv-templates.md`
- `.claude/skills/job-application-assistant/06-cover-letter-templates.md` (only if a cover letter is required or explicitly requested)

### CV (`cv/jobs/<date>/<company-slug>/source.html`)
- Copy the base template chosen in Step 1 (see `05-cv-templates.md` for the available templates and the font-path math for this folder depth)
- Tailor: rewrite the profile paragraph for this specific role; reorder/rewrite competency bullets to front-load what this posting cares about; cut low-relevance entries entirely rather than trimming everything a little (see `05-cv-templates.md` → CV Content Judgement and Relevance-weighted cutting)
- Keep to exactly 2 pages — see the hard page budget in `05-cv-templates.md`

### Cover Letter (`cv/jobs/<date>/<company-slug>/cover.html`)
- **Only write one if the posting explicitly requires it, or the user explicitly asks for one regardless.** Do not write a cover letter by default — see `CLAUDE.md` Step 4.
- Match the language of the job posting
- Follow the structure and the critical writing rules (no em-dashes in prose, no fabricated claims, honest disclosure of genuine gaps — see "Honest gap disclosure" in `03-writing-style.md`) from `03-writing-style.md` and `06-cover-letter-templates.md`
- Address to a named contact if one was captured in Step 0, otherwise the company/team
- Keep to one page

Write both files to disk. Keep the exact text of both drafts in working memory — you will pass them inline to the reviewer in Step 3 and revise them in Step 4 without re-reading.

---

## Step 3: REVIEWER - Research & Critique

Use the **Agent tool** to spawn a `general-purpose` reviewer agent. The reviewer gets a fresh context, so pass the drafts **inline in the prompt** below (do not make the reviewer Read them). Scope the reviewer's file reads to content-critique essentials only — the reviewer does not need the template files (`05`, `06`) since those govern structural/layout concerns the drafter already applied.

Replace `<COMPANY>`, `<ROLE>`, `<INSERT_JOB_POSTING_TEXT_HERE>`, `<INSERT_CV_DRAFT_HERE>`, and `<INSERT_COVER_LETTER_DRAFT_HERE>` with actual values before dispatching. Omit the cover letter block entirely if none was written.

```
You are a hiring manager proxy reviewing a job application. Your job is to make the application as targeted and compelling as possible.

## Your Tasks

### 1. Research the Company
Use WebSearch and WebFetch to research:
- The company's website, mission, and recent news
- The specific department or team (if mentioned in the posting)
- Any recent projects, press releases, or strategic initiatives relevant to the role
- Company culture and values

### 2. Read Reference Materials (content-critique only)
Read these files — and only these — to ground your critique:
- `.claude/skills/job-application-assistant/01-candidate-profile.md`
- `.claude/skills/job-application-assistant/02-behavioral-profile.md` — use this specifically to check whether the cover letter's voice matches the candidate's natural register. Don't give a collaborative, low-ego profile a combative, solo-hero tone; don't give a confident, direct profile over-hedged, apologetic phrasing.
- `.claude/skills/job-application-assistant/03-writing-style.md`
- `.claude/skills/job-application-assistant/04-job-evaluation.md`

Do NOT read `05-cv-templates.md` or `06-cover-letter-templates.md` — those govern HTML/layout structure the drafter already applied and are not needed for content critique.

### 3. Drafts to Review
Drafts are provided inline below. Do NOT use the Read tool on the draft files — use these exact texts.

<CV_DRAFT file="cv/jobs/<date>/<COMPANY>/source.html">
<INSERT_CV_DRAFT_HERE>
</CV_DRAFT>

<COVER_LETTER_DRAFT file="cv/jobs/<date>/<COMPANY>/cover.html">
<INSERT_COVER_LETTER_DRAFT_HERE>
</COVER_LETTER_DRAFT>

### 4. Job Posting
<JOB_POSTING>
<INSERT_JOB_POSTING_TEXT_HERE>
</JOB_POSTING>

### 5. Produce Feedback

Return your feedback in **two parts**:

**Part A — Structured edits (preferred format whenever possible):**
A JSON array of concrete edits the drafter can apply directly without re-reading the files. Each edit is an object:
```json
{
  "file": "cv/jobs/<date>/<COMPANY>/source.html" | "cv/jobs/<date>/<COMPANY>/cover.html",
  "old_string": "<exact text currently in the draft>",
  "new_string": "<replacement text>",
  "reason": "<one-line rationale: keyword match / company angle / reframing / style>"
}
```
Only use this format when you can quote the exact `old_string` from the drafts above. Make `old_string` unique — include enough surrounding context so it matches exactly once per file.

**Part B — Narrative suggestions (for judgment calls that are not mechanical edits):**
Prose suggestions grouped by category. Produce each category even if your finding is "no issues" — silence on a category can be mistaken for skipping it.
- **Missed keywords/requirements** — what to add and roughly where, if it cannot be expressed as a clean string replacement
- **Company/department-specific angles** — connections between experience and the company's strategic priorities, based on your research
- **Action-oriented reframing** — identify passive, generic, or low-energy statements and suggest action-oriented rewrites. Use this category especially for structural weakness that doesn't fit a single-sentence swap.
- **Tone, style, and AI-tell issues** — check against `03-writing-style.md` AND `02-behavioral-profile.md`. Flag cliches, hedging, over-humility, inconsistent register, any mismatch between the letter's voice and the candidate's natural register, AND any stock phrase or sentence-structure pattern repeated more than once or twice across the document (a hallmark of AI-generated text — see "Human-sounding writing" in `03-writing-style.md`). Phrases drawn directly from the job posting's own language are not a flaw — only flag repetition that comes from generic stylistic habit.

**CRITICAL RULE:** All suggestions must be grounded in actual profile data. Do NOT suggest fabricating skills, experience, or achievements. If a requirement is a gap, say so honestly and suggest how to frame adjacent experience instead, or how to disclose it directly (see "Honest gap disclosure" in `03-writing-style.md`).

Do **not** run a verification checklist — the drafter will do that in the final step. Focus on content critique.

Return Part A and Part B together as a single structured message.
```

---

## Step 4: DRAFTER - Revise Based on Feedback

Once the reviewer agent returns its feedback:

1. **Apply Part A (structured edits) directly with the Edit tool.** Do NOT re-read the draft files — you already have them in context from Step 2, and the reviewer's `old_string` values were quoted from that same text. For each edit in the JSON array, call `Edit` with the given `file`, `old_string`, and `new_string`. Skip any whose rationale would require fabricating content.
2. **Apply Part B (narrative suggestions)** using judgment. These need interpretation, not mechanical replacement:
   - **Missed keywords/requirements:** add the keyword or capability where it fits naturally. Prefer the experience bullets (concrete evidence) over the profile statement (abstract claim).
   - **Company/department-specific angles:** weave the reviewer's research into the cover letter opening or motivation paragraph. Verify every company claim via WebFetch/WebSearch before including it — do not trust reviewer research at face value.
   - **Action-oriented reframing:** rewrite passive or generic phrasing.
   - **Tone, style, and AI-tell issues:** apply the writing-style-guide fixes (no em-dashes in prose, no cliches, no apologetic hedging, consistent first-person active voice, vary any repeated stock phrase or sentence structure).
3. Do NOT incorporate any suggestion that would fabricate skills or experience. If a posting requirement is a genuine gap, acknowledge it honestly and frame adjacent experience instead.

After all edits are applied, the two files on disk are the final drafts.

---

## Step 5: DRAFTER - Compile & Inspect PDFs (MANDATORY)

**Never skip this step.** The HTML looking fine is not sufficient — Chrome's pagination is unpredictable and commonly produces broken layouts (orphaned job titles separated from their bullets, content spilling to an extra page, a font silently falling back to Arial because a relative path is off by one directory level). Compile both documents and visually verify the PDFs before presenting.

### 5a. Compile

Pick the command for the OS you are on (on Windows use the **PowerShell tool**, not Bash):

```powershell
powershell -File cv/compile.ps1 <input.html> <output.pdf>     # Windows
```
```bash
bash pipeline/compile.sh <input.html> <output.pdf>            # macOS / Linux
```
Per-OS detail (calling Chrome directly, the Node route, the mandatory flags):
`.claude/skills/job-application-assistant/05-cv-templates.md` → "Compile command".

Repeat for `cover.html` → `<Name>_Cover.pdf` if one was written. If Chrome reports "PDF not generated" on the first attempt, retry once — this is an intermittent timing issue, not a real failure.

### 5b. Inspect layout

Read both PDFs via the Read tool and verify:

**CV:**
- [ ] Exactly 2 pages (not 1, not 3)
- [ ] No orphaned entry titles — a job/education title must never sit alone at the bottom of one page with its bullets pushed to the next
- [ ] No Chrome header/footer artifacts (date, URL, page number) — if present, check `@page { size: A4; margin: 0; }` is intact in the template (a non-zero `@page margin` is what triggers these overlays)
- [ ] Fonts render correctly, not a silent fallback to a system font (a wrong relative font path fails with no error — see `05-cv-templates.md` for the path math)

**Cover letter (if written):**
- [ ] Exactly 1 page
- [ ] Signature block visible, not cut off

### 5c. Iterate until clean

If the layout has problems, edit the HTML and recompile. See `05-cv-templates.md` and `06-cover-letter-templates.md` for the page-break fixes and relevance-weighted cutting approach. Do not shrink font size or padding to force-fit content — cut content instead.

Do not proceed to Step 6 until both PDFs pass inspection.

---

## Step 6: Update Tracking

Update `seen_jobs.json` (the application tracker) with status, the CV path, the cover letter path if one exists, and every application detail captured in Step 0.

Append a "CV Tailoring" section to `job_context.txt` recording: which base template was used, the angle taken in the profile paragraph, which entries were emphasized or front-loaded and why, and what was cut or trimmed and why. If a cover letter was written, also record its addressing and framing decisions. This is the audit trail showing the job data from Step 0 actually drove the content.

---

## Step 7: Present Final Output

Run the full verification checklist from `CLAUDE.md` now — this is the **only** verification pass in the workflow.

### Verification Checklist
Report pass/fail for each item in the CLAUDE.md verification checklist (factual accuracy, targeting, writing quality, PDF layout, application tracking).

### Key Tailoring Decisions
Summarize 3-5 key decisions made to tailor the application:
- What was emphasized and why
- What company-specific angles were incorporated
- What the reviewer suggested that was most impactful
- Any gaps that were acknowledged or reframed

### Files Created
List the files written, e.g.:
- `cv/jobs/<date>/<company-slug>/source.html` and `<Name>_CV.pdf`
- `cv/jobs/<date>/<company-slug>/cover.html` and `<Name>_Cover.pdf` (if written)
- `cv/jobs/<date>/<company-slug>/job_context.txt`

Tell the user the files are ready for review. **Never move the folder to an "applied" location, and never mark the tracker status as "applied," without the user explicitly confirming the application was submitted** — see `CLAUDE.md` Repo Structure for the `cv/jobs/` → `cv/applied/` convention.
