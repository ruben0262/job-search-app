# /reset - Reset Candidate Profile Data

You are resetting parts of the job search framework back to a blank state so the user can start fresh with `/setup`.

**This command is destructive.** Nothing is deleted until the user explicitly confirms. Follow these steps exactly in order.

---

## Step 0: Parse Scope from Arguments

Check `$ARGUMENTS` for a scope keyword:

- `profile` — clears candidate profile data from skill files only
- `documents` — deletes user-provided files from the `documents/` folder only
- `all` — both of the above

If `$ARGUMENTS` is empty or does not contain a recognized scope keyword, ask:

> **What would you like to reset?**
>
> - **`profile`** — Clears candidate data from the skill files (profile, behavioral, STAR examples), the Candidate Profile section of `CLAUDE.md`, and the base HTML CV templates in `cv/`. The framework structure and writing rules are preserved. Use this to re-run `/setup` from scratch, or to hand this repo off to someone else.
>
> - **`documents`** — Deletes all files you've placed in the `documents/` folder (CV PDFs, LinkedIn export, diplomas, references, past applications). The folder structure and `README.md` are preserved.
>
> - **`all`** — Both of the above.
>
> Reply with `profile`, `documents`, or `all`.

Wait for the user's response before continuing.

---

## Step 1: Show Exactly What Will Be Cleared

Before doing anything, show the user precisely what will be wiped.

### If scope includes `profile`:

Read the current state of these files and report whether each has content or is already empty:

- `.claude/skills/job-application-assistant/01-candidate-profile.md`
- `.claude/skills/job-application-assistant/02-behavioral-profile.md`
- `.claude/skills/job-application-assistant/05-cv-templates.md` *(Role-Type Lens framing only — framework structure is preserved)*
- `.claude/skills/job-application-assistant/07-interview-prep.md` *(STAR examples and STAR candidates sections only — framework structure is preserved)*
- `CLAUDE.md` *(Candidate Profile section only — workflow, repo structure, and verification checklist are preserved)*
- `cv/base_cv.html` *(contains the user's real CV content; reset to the placeholder scaffold, do not delete)*

Present as:

```
## Profile reset will clear:

- 01-candidate-profile.md — [has content / already empty]
  Full file will be replaced with a blank template.

- 02-behavioral-profile.md — [has content / already empty]
  Full file will be replaced with a blank template.

- 05-cv-templates.md — [has personalized Role-Type Lens / already generic]
  The "CV Content Judgement — Tailoring by Role-Type Lens" section will revert to its generic domain-A/domain-B
  framing. The rest of the file (compile pipeline, page budget, cutting rules) is preserved.

- 07-interview-prep.md — [has STAR examples / already blank]
  STAR examples and any STAR candidate stubs will be cleared. Framework, tough questions, and roleplay guidelines are preserved.

- CLAUDE.md — [has a populated Candidate Profile / already blank]
  The Candidate Profile section (Identity, Education, Professional Experience, Technical Skills, Certifications,
  Publications, Target Roles, Geographic Scope, Deal-breakers, CV Header Location Rule) will be cleared. Workflow
  steps, repo structure, and the verification checklist are preserved.

- cv/base_cv.html — [populated with real content, or "already a blank scaffold"]
  Each base CV template will be deleted (they contain one person's real name, contact details, and work history;
  the next user generates their own via `/setup`).

The following files are NOT touched (they contain framework rules, not candidate data):
  - 03-writing-style.md
  - 04-job-evaluation.md
  - 06-cover-letter-templates.md
```

### If scope includes `documents`:

Use Glob to list all files present in `documents/cv/`, `documents/linkedin/`, `documents/diplomas/`, `documents/references/`, and `documents/applications/`. Present as:

```
## Documents reset will delete:

documents/cv/
  - [filename] or "(empty)"

documents/linkedin/
  - [filename] or "(empty)"

documents/diplomas/
  - [filename] or "(empty)"

documents/references/
  - [filename] or "(empty)"

documents/applications/
  - [subfolder/filename] or "(empty)"

documents/README.md — NOT deleted (instructions file)
```

If all document subfolders are already empty, state "All document subfolders are already empty — nothing to delete." and skip the confirmation step for this scope.

---

## Step 2: Require Explicit Confirmation

Present the confirmation prompt:

> **This cannot be undone.**
>
> Type **`RESET`** (all caps) to confirm, or anything else to cancel.

Wait for the user's response.

- If the user types exactly `RESET`: proceed to Step 3.
- If the user types anything else: abort and tell them "Reset cancelled. Nothing was changed."

---

## Step 3: Execute the Reset

### Profile reset

**For `01-candidate-profile.md`**, replace the file content with:

```markdown
# Candidate Profile

<!-- Run /setup to populate this file -->

## Identity

## Education

## Professional Experience

## Independent Projects

## Technical Skills

## Publications

## Awards

## References
```

**For `02-behavioral-profile.md`**, replace the file content with:

```markdown
# Behavioral Profile

<!-- Run /setup to populate this file -->

## Overview

## Strongest Behavioral Traits

## How I Work Best

## Growth Areas

## Mapping to Job Posting Language

## Management Style Preferences

## Using This in Applications
```

**For `05-cv-templates.md`**, locate the `## CV Content Judgement — Tailoring by Role-Type Lens` section. Replace
the personalized framing inside it (the bullets naming the user's actual domains) back with the generic two-domain
example:

```markdown
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
```

Leave all other content in `05-cv-templates.md` intact.

**For `CLAUDE.md`**, locate the `## Candidate Profile` section (Identity, Education, Professional Experience,
Technical Skills, Certifications, Publications, Target Roles (priority order), Geographic Scope, CV Header
Location Rule, Deal-breakers). Replace its contents with:

```markdown
## Candidate Profile

<!-- Run /setup to populate this section -->

### Identity

### Education

### Professional Experience

### Technical Skills

### Certifications

### Publications

### Target Roles (priority order)

### Geographic Scope

### CV Header Location Rule

### Deal-breakers
```

Leave every other section of `CLAUDE.md` (Role, Repo Structure, Job Application Workflow, CV Compilation, CV
Content Judgement, Verification Checklist) intact — those are framework rules, not candidate data.

**For `cv/base_cv.html`**, restore it to the blank placeholder scaffold — do NOT delete the file (the pipeline
and `/apply` both read this exact path, and `/setup` fills it in place). Keep the `<style>` block, `@page` rule,
font-face paths, class names and section order byte-for-byte; replace only the content: `[YOUR NAME]`,
`[CITY, COUNTRY]`, `[PHONE]`, `[EMAIL]`, `[LINKEDIN URL]`, `[GITHUB URL]` in the header, and bracketed
placeholder text for the profile paragraph, competency bullets, experience entries, education and
certifications. If `git` history is available, `git checkout <first-commit> -- cv/base_cv.html` is the reliable
way to get the pristine scaffold back.

**For `07-interview-prep.md`**, locate and remove:
- The entire `## Ready-Made STAR Examples` section and all numbered STAR examples under it
- Any `## STAR Candidates (Complete Manually)` section added by `/setup` Path A

Replace with:

```markdown
## Ready-Made STAR Examples

<!-- Run /setup to populate STAR examples from your actual experience -->
```

Leave all other content in `07-interview-prep.md` intact (STAR format explanation, tough questions, questions to ask interviewers, phone/video tips, follow-up etiquette, roleplay guidelines).

### Documents reset

For each non-empty document subfolder, delete all files within it using Bash `rm`. Do not delete the folder itself, and do not delete `documents/README.md`.

```bash
rm -f documents/cv/*
rm -f documents/linkedin/*
rm -f documents/diplomas/*
rm -f documents/references/*
rm -rf documents/applications/*/
```

---

## Step 4: Confirm What Was Done and Next Steps

After the reset is complete, report:

```
## Reset complete

### Cleared
[List each file/folder that was actually modified or cleared]

### Unchanged
[List anything that was already empty or was intentionally preserved]
```

Then tell the user what to do next based on what was reset:

**If profile was reset:**
> Your candidate profile is now blank. Run `/setup` to repopulate it. The command auto-detects any files in your `documents/` folder and offers to read from there; otherwise it walks you through a CV import or interactive interview.

**If documents were reset:**
> The `documents/` folder is now empty. Add your career documents and run `/setup` to populate your profile. See `documents/README.md` for instructions on what to put where.

**If both were reset:**
> Both your profile files and documents folder are now empty. Add documents to `documents/` (or skip and use the CV import / interview path), then run `/setup`.
