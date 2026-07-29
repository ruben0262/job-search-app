# /setup-prompts - Personalize the Document-Writing Prompts

You are tuning the prompts that write the user's **CVs, cover letters, cold emails and
interview crib sheets** (`pipeline/prompts/`). These are the files that turn a job posting into
the documents an employer actually reads, so their voice and rules decide what lands.

The prompts ship **field-agnostic and already good** — they encode the callback-maximizing
rules (ATS strategy, the 6-second test, the bullet formula, show-don't-tell, human-sounding
writing, no fabrication). **Those rules stay for everyone.** This command is not a rewrite: it
is a short interview to set the handful of things that genuinely differ per person, plus an
optional real-output check.

**Prerequisite:** `/setup` must be done (`CLAUDE.md` has no `[FILLED BY /setup]` placeholders).
The prompts deliberately defer every candidate FACT to `CLAUDE.md` — it is auto-loaded into
these calls and is authoritative. Never copy profile facts into a prompt: that duplicates the
truth in two places and they drift.

---

## Step 0: Read before you touch anything

- `pipeline/prompts/tailor_cv.md` (the big one), `tailor_cover.md`, `outreach.md`,
  `interview.md`
- `CLAUDE.md` → "CV Content Judgement" (the per-user angle rules the prompts defer to)
- `docs/PROJECT_STATE.md` → "CV / cover / outreach / interview builds"

**Do NOT touch** the `<PLACEHOLDERS>` (`<JOB_CONTEXT>`, `<BASE_TEMPLATE>`, `<CV_HTML>`,
`<CV_TEXT>`, `<HEAD_BLOCK>`): Python substitutes them at build time. Breaking one breaks builds.

**Do NOT weaken** the MOST COMMON FAILURES / HARD RULES / SELF-CHECK blocks. Each line exists
because a real generated document failed that way. Add to them; do not delete.

## Step 1: Interview

One compact message per topic; pre-fill from `CLAUDE.md` and ask to confirm.

1. **Spelling convention.** British or American (or another regional standard)? The prompts
   currently say "use the convention in CLAUDE.md; if none, match the posting's region".
   Record their choice in `CLAUDE.md` (CV Content Judgement) — that is the single place the
   four prompts read it from. Consistency matters more than the choice: mixed spelling reads
   as careless.
2. **Application language(s).** If they apply in more than one language, state the rule
   explicitly (e.g. "CV follows the posting's language unless it says English is accepted") and
   record it in `CLAUDE.md` → Geographic Scope. Confirm it matches `JOB_LANGUAGES` in `.env` —
   a language they cannot write a CV in should not be scraped in the first place.
3. **Contact links on the CV.** Which of these appear in the CV header, and with what URL:
   email, phone, LinkedIn, GitHub, portfolio/personal site, Google Scholar, ORCID, Behance/
   Dribbble, StackOverflow? Ask for the exact URLs. Field matters: a designer's portfolio and a
   researcher's Scholar profile are as load-bearing as GitHub is for a developer. **These live
   in the `cv/base_cv.html` header, not in a prompt** — update the header directly and drop any
   line they do not use (an empty placeholder link on a CV looks broken).
4. **Header location rule.** Confirm the "CV Header Location Rule" in `CLAUDE.md` is filled and
   correct — it decides which city the CV header shows per job, and it is a work-eligibility
   signal (see the rule's own rationale). The prompts apply it; they do not define it.
5. **Cover letter length and tone.** Default: ~200–260 words, warm, no contractions, small
   2–3 sentence paragraphs. Some fields expect more formality or a different length —
   confirm or adjust in `tailor_cover.md` (STRUCTURE + the self-check line).
6. **The anecdote rule.** `tailor_cover.md` permits a short, plausibly-reconstructed anecdote
   as long as it is consistent with their real experience and defensible in an interview
   (fabricated hard facts are never allowed). Some people are uncomfortable with any
   reconstruction — ask. If they want strict CV-only sourcing, tighten THE HUMAN TOUCH to
   "only incidents explicitly evidenced in the CV/profile".
7. **Terminology sensitivities.** Any term in their background that is divisive or loaded
   outside its own niche (an asset class, a methodology, a politically-charged domain)? Record
   the rule in `CLAUDE.md` (CV Content Judgement) — the prompts already say to follow it: use
   the loaded term freely only for roles native to that domain, neutral phrasing elsewhere.
8. **Banned phrases.** The prompts ban a list of clichés ("leveraging", "robust", "passionate",
   "end-to-end", …). Ask if they have their own pet-hate words, or any on the list they
   actually want (rare, but a real term in some fields — e.g. "robust" in statistics).
9. **Outreach sign-off.** `outreach.md` and `tailor_cover.md` take the sign-off name and
   contact line from the CV header. Confirm the header is right (Q3) — that is the fix, not a
   prompt edit.

## Step 2: Apply the changes

Route each answer to its correct home. Getting this right is the point of the command:

| Answer | Goes in |
|---|---|
| Spelling, language rule, terminology sensitivities, per-angle rules | `CLAUDE.md` (the prompts read it) |
| Contact links, header location default | `cv/base_cv.html` header |
| Cover length/tone, anecdote strictness, banned-phrase list | the relevant prompt file |
| Scrape languages | `.env` `JOB_LANGUAGES` |

Keep prompt edits surgical: change the specific line, preserve structure and the numbered
FAILURES/RULES/SELF-CHECK blocks.

## Step 3: Verify with a real build (strongly recommended)

A prompt reads fine and still produces a bad document — only output tells you. If the stack is
running and a job is available:

```
docker compose run --rm pipeline python -m pipeline.cv_builder --url <a real job posting URL> --cover
```

Read the PDF **and** the cover with the user, checking:
- Does the profile paragraph pass the 6-second test — would a recruiter want to meet them?
- Is every experience bullet a result (verb + achievement + number), not a task?
- Is anything in there **not true**? Fabrication is disqualifying: if you see an invented
  employer, metric, or a "5+ years" garnish, the cause is almost always missing grounding —
  check `CLAUDE.md` is populated, and read the CALL SHAPE note in `pipeline/cv_builder.py` for
  the measured reason these calls keep CLAUDE.md context and default thinking.
- Does the cover open by restating the posting, or by showing real work? (The former is the
  single most common failure.)
- Does it sound like a person, or like a template?

Fix what you find at its source (usually `CLAUDE.md` or `cv/base_cv.html`, not the prompt), and
re-run. Note: `cv/base_cv.html` is the ceiling — tailoring only ever CUTS from it, so anything
missing there can never appear on any document.

## Step 4: Report

Summarize what changed and where, per the Step 2 table. Explicitly confirm you did NOT weaken
the shared quality rules — they are the reason the documents work, and they belong to every
user of this repo, not just this one.
