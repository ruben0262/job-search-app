# CV Templates and Tailoring Guide

## Template: HTML + Chrome headless

CVs use plain HTML/CSS, compiled to PDF with Chrome headless. **Do not use LaTeX** for CVs in this framework —
HTML/Chrome is simpler to tailor per-application and avoids toolchain fragility (missing packages, font-expansion
errors) that LaTeX distributions are prone to.

**Base template:** exactly ONE — `cv/base_cv.html` (`/setup` writes your profile into it). Every application is
a tailored copy of this file: the angle, emphasis and competency framing are decided per posting at tailoring
time, so there is no template-per-role-type to keep in sync. Per-angle rules (which experience leads for which
kind of role, what to cut) live in CLAUDE.md's "CV Content Judgement" section.

**The structure is canonical — an imported CV never overrides it.** The base template uses the shipped
scaffold's single-column, recruiter-standard layout (header → profile → core competencies → experience →
education → certifications/languages) with its exact `<style>` block and class names — the automated pipeline
restores that CSS on every build and depends on those classes. A CV the user brings in is a content source
(facts, numbers, history), never a structure source: no photos, multi-column layouts, graphics, or custom
section orders survive the import, and every imported bullet is rewritten to the bullet formula
(STRONG VERB + SPECIFIC ACHIEVEMENT + QUANTIFIED IMPACT) before it enters the base template.
**Output file:** `cv/jobs/<YYYY-MM-DD>/<company-slug>/source.html` → `cv/jobs/<YYYY-MM-DD>/<company-slug>/<Name>_CV.pdf`
(date = the day the job was captured; see `CLAUDE.md` Repo Structure for the full folder convention, including
any legacy flat folders predating the date-folder structure that are not migrated)
**Compile with:** Chrome headless. Pick the helper for the OS you are on (see "Compile command" below) —
`cv/compile.ps1` on Windows, `pipeline/compile.sh` on macOS/Linux. On Windows, invoke it **via the PowerShell
tool, not Bash** — Bash invocations have failed in this repo even when Chrome is correctly installed;
PowerShell works reliably (retry once if it reports "PDF not generated", an intermittent timing issue).
**Font directory:** `cover_letters/OpenFonts/fonts/` (shared by CVs and cover letters; this directory itself does
not move when the date-folder structure is used). From `cv/jobs/<date>/<slug>/source.html` the relative
path is four levels up: `../../../../cover_letters/OpenFonts/fonts/<family>/...`. From a legacy flat
`cv/jobs/<slug>/source.html` it's three levels up — check which structure a given folder uses before copying paths.

### Compile command

Detect the OS first and use the matching helper — they are equivalent (same Chrome flags), they just differ in
how Chrome is invoked.

**Windows** (via the PowerShell tool):
```powershell
powershell -File cv/compile.ps1 cv/jobs/<date>/<slug>/source.html cv/jobs/<date>/<slug>/<Name>_CV.pdf
```

**macOS / Linux** (via Bash):
```bash
bash pipeline/compile.sh cv/jobs/<date>/<slug>/source.html cv/jobs/<date>/<slug>/<Name>_CV.pdf
```
`compile.sh` calls `google-chrome`. On macOS, or if that binary is not on PATH, call Chrome directly:
```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=old \
  --print-to-pdf="$PWD/cv/jobs/<date>/<slug>/<Name>_CV.pdf" --no-sandbox \
  --print-to-pdf-no-header --disable-gpu "file://$PWD/cv/jobs/<date>/<slug>/source.html"
```
(Chromium works too: `chromium` / `chromium-browser`. A third option, if you have Node:
`node cv/compile_cv.js <input.html> <output.pdf>` — needs `npm i puppeteer-core`.)

**The flags are not optional.** `--print-to-pdf-no-header` is what keeps Chrome from stamping a date/URL onto
the page, and `--headless=old` is the mode that honours the print CSS. Whatever route you take, keep them.

**Critical CSS rule:** `@page { size: A4; margin: 0; }` with all spacing handled via `body { padding: ... }`.
Using `@page { margin: Xmm; }` causes Chrome to add date/URL header-footer overlays. The base template already
has this correct — preserve it when tailoring.

## Compile-and-Inspect Loop (MANDATORY)

After writing the CV and before presenting to the user, always compile and visually inspect the PDF (Read tool).
Iterate until clean:

1. Compile via the command above (PowerShell tool)
2. Confirm page count is exactly 2 — not 1, not 3
3. Read the PDF and visually check: no orphaned entry titles (title at the bottom of page 1, bullets pushed to
   page 2), no Chrome header/footer artifacts, the configured font renders (not falling back to a system font — a
   wrong font path fails silently with no error)

### Fixing common page-break problems

**Problem: entry title on page 1, bullets orphaned to page 2**
The `.entry` class in the base template already has `break-inside: avoid; page-break-inside: avoid;` — this
should prevent most orphaning automatically. If it still happens, the entry block is too long to fit as a unit;
trim a bullet from it rather than fighting the page break.

**Problem: one trailing section spills to page 3 (e.g., References alone on page 3)**
This is common and not itself a defect — check whether it's just a short trailing section sitting alone on page
2 of a clean 2-page document (acceptable) versus genuine 3-page overflow (not acceptable, cut content).

**Problem: 3 pages with real content on page 3**
Cut content — do not shrink `font-size` or `padding` to force-fit. See "Relevance-weighted cutting" below.

**Problem: content finishes early on page 2 (feels thin)**
Restore the highest-relevance item that was previously cut — a CV that ends mid-page 2 looks incomplete.

## CV Content Judgement — Tailoring by Role-Type Lens

Templates define layout only. Every CV is written fresh for the specific role. Before writing any bullet ask:
"Would the hiring manager for this exact role find this impressive?" If not, cut it. No filler, no copy-paste, no
space wasted.

**Identify the 5-7 things the hiring manager actually cares about from the job description, then write every
sentence toward those.**

The candidate's experience usually spans more than one domain (e.g. an engineering track and a research/finance
track, or a technical track and a client-facing track). The same underlying experience should be **framed through
a different lens** depending on which role type a given application targets — this is the core tailoring skill,
not just keyword-matching.

- **For a role in domain A** (e.g. engineering/dev): describe domain-B experience (e.g. finance, research)
  through the domain-A lens — what was built, what stack, what system/process decisions were made. Cut the
  domain-B-specific jargon, tools, and metrics that mean nothing to a domain-A hiring manager.
- **For a role in domain B**: invert it. Domain-B depth is the headline; domain-A experience is condensed to
  "built the infrastructure to do the work," not the focus.
- **For a hybrid role** that explicitly wants both: lead with whichever domain the posting weights more heavily,
  and keep just enough of the other domain to show range, framed as supporting evidence not the headline.

When `/setup` populates this file for a given user, replace this generic A/B framing with their actual concrete
domains (the candidate profile and target roles in `01-candidate-profile.md` and `04-job-evaluation.md` name
them) and spell out exactly what to cut and what to keep per domain, the way a domain expert would.

### Avoid divisive or niche-coded terminology in the wrong context

If part of the candidate's background involves a topic with a polarized public reputation (a specific
asset class, technology, or methodology that some audiences view skeptically), avoid that term by name in
applications **outside that niche** — describe the underlying work neutrally (the asset class, the technique,
the platform) instead. Use the term freely only when applying **to a role inside that same niche**, where it
signals credibility rather than risk.

### Never include
- Anything the candidate has explicitly said to permanently exclude from CVs (check `01-candidate-profile.md` for
  any such notes before reusing old CV content as a starting point).

### General rule
When in doubt: weight toward the lens of the role actually being applied to, not toward showing the full breadth
of the candidate's background. The candidate can genuinely back every claim, so write confidently rather than
hedging — but never claim a domain of work that wasn't actually performed (see `03-writing-style.md` → Honest gap
disclosure for how to handle a real gap instead of glossing over it).

## Section-by-Section Tailoring

### Profile Paragraph (Best Practice)
The most important section to customize — the `<p class="profile">` right after the header. Write 3-5 lines
functioning as an elevator pitch: concise, compelling, explaining why you're qualified for *this specific role*.
Focus on what the employer gains from hiring you, not a generic summary.

### Core Competencies (Best Practice)
Reorder and rewrite the `<li>` bullets in `.competencies ul` based on the role. Keep bold category labels
(`<strong>Label:</strong>`). 5-7 competency bullets, front-loading the ones most relevant to the posting.

### Experience Entries
- Rewrite bullet points to emphasize aspects most relevant to the target role
- Use 4-6 bullets for the most recent/most relevant role, 2-3 for older or less relevant roles
- Cut entries with no relevance to the target role entirely (see CV Content Judgement above)
- **Emphasize measurable results** where genuinely true: "Built X serving Y", "Reduced Z by N%"

### Education
- Keep brief: dates, degree, institution; include the dissertation/thesis line only when relevant to the role

### References
- "Available upon request." — do not list named references or attach reference letters

## Page Budget — Hard 2-Page Limit

The CV **must** compile to exactly 2 pages. Use these content limits as a guide:

| Section | Max budget |
|---------|-----------|
| Profile paragraph | 3-5 lines |
| Core Competencies | 5-7 bullets, 1-2 lines each |
| Most recent/most relevant role | 4-6 bullets |
| Other roles | 2-3 bullets each |
| Education | 1-2 entries |
| Languages / Certifications | 1 line each |
| References | "Available upon request." (single line) |

**If in doubt, cut rather than squeeze.** Shrinking font size or padding to force-fit content makes the CV look
cramped and is never the right fix.

## Relevance-weighted cutting (the right way to shrink a CV)

**Cut by signal, not by section.** An older-role bullet that speaks directly to the posting is worth more than
a recent-role bullet that does not.

For every candidate line, score three things:

1. **Relevance to THIS posting** — does the line hit a named tool, keyword, or stated responsibility in the job ad?
2. **Uniqueness** — is it the only place this claim appears, or duplicated elsewhere in the CV?
3. **Narrative load** — does a cover letter (if one exists for this job) depend on it?

Cut the lowest-total-score line first, regardless of which section it sits in.

### Practical order of cuts (easiest → last resort)

1. **Redundancy.** If a claim appears in both Core Competencies and a role bullet, cut the Core Competencies
   version (the experience bullet is more concrete evidence).
2. **Low-relevance experience bullets/entries.** A bullet or whole entry about work that doesn't touch posting
   keywords. See CV Content Judgement above for which entries are cut entirely per role-type lens.
3. **Low-relevance supporting content.** A certification that doesn't touch the posting's stack.
4. **Last-resort structural cuts.** Tightening an older role to 2 bullets, dropping the dissertation line.

### Pitfalls to avoid

- Do not cut the one concrete example a cover letter (if any) leans on.
- Do not cut to fit if the fit is borderline. Prefer trimming a single low-value bullet over restructuring.

## Recommended Section Order

The base template already orders sections correctly for most roles (Profile → Competencies → Experience →
Education → Languages → Certifications → References). Only reorder if a specific posting clearly calls for
leading with education (e.g., a graduate scheme with a strict university filter).
