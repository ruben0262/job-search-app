# Cover Letter Templates and Tailoring Guide

## Template: HTML + Chrome headless (same pipeline as CVs)

Cover letters use the same HTML/CSS approach as the CVs, compiled to PDF with Chrome headless.
**Do not use LaTeX/XeLaTeX for cover letters** — this repo used to have a `cover.cls` LaTeX template;
it has been retired and must not be reintroduced.

**Output file (from 2026-06-22 onward):** lives **inside the same job folder as the CV**, not a separate
`cover_letters/<slug>/` folder — `cv/jobs/<YYYY-MM-DD>/<company-slug>/cover.html` →
`cv/jobs/<YYYY-MM-DD>/<company-slug>/<Name>_Cover.pdf`. This keeps everything for one application (CV, cover letter,
job_context.txt) in one place. Record the PDF path in `seen_jobs.json` under a `"cover_letter"` field.
(Legacy cover letters built before this date remain at the old root-level `cover_letters/<slug>/cover.pdf` path
— do not move them.)
**Compile with:** Chrome headless, via `cv/compile.ps1`, **using the PowerShell tool, not Bash** (Bash invocations
of this script have failed in this repo even with Chrome correctly installed; retry once via PowerShell if it
reports "PDF not generated" — intermittent timing issue, not a real failure).
**Font directory:** `cover_letters/OpenFonts/fonts/` (reuse the same `@font-face` Lato block as the CV templates;
this shared asset directory did not move). From `cv/jobs/<date>/<company-slug>/cover.html` the relative path is
`../../../../cover_letters/OpenFonts/fonts/lato/...` (four levels up — same depth as the CV's `source.html` in
the same folder). From a legacy `cover_letters/<slug>/cover.html` it's one level up, `../OpenFonts/fonts/lato/...`.

### Compile command

```powershell
powershell -File cv/compile.ps1 cv/jobs/<date>/<slug>/cover.html cv/jobs/<date>/<slug>/<Name>_Cover.pdf   # Windows
```
```bash
bash pipeline/compile.sh cv/jobs/<date>/<slug>/cover.html cv/jobs/<date>/<slug>/<Name>_Cover.pdf          # macOS / Linux
```
Per-OS detail and the mandatory Chrome flags: `05-cv-templates.md` → "Compile command".

If Chrome reports "PDF not generated" on the first attempt, retry once — this has been an intermittent
timing issue, not a real failure.

**Critical CSS rule (same as CVs):** `@page { size: A4; margin: 0; }` with all spacing via `body { padding: ... }`. Using `@page { margin: Xmm; }` causes Chrome to add date/URL header-footer overlays.

## Compile-and-Inspect Loop (MANDATORY)

After writing the cover letter and before presenting to the user, always compile and visually inspect the PDF (Read tool). Iterate until clean:

1. Compile via the command above
2. Confirm page count is exactly 1
3. Read the PDF and visually check: no text cut off, no Chrome header/footer artifacts, Lato renders (not falling back to Arial — a wrong font path fails silently)

## Document Structure

Reuse the CV header style (name in large bold blue, contact line, horizontal rule), then a date line, salutation, body paragraphs, an optional bullet list of concrete achievements, and a closing/signature. The skeleton below is the complete structure — fill the identity fields from your profile.

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>[YOUR NAME] – Cover Letter – [Company]</title>
<style>
@font-face { font-family: 'Lato'; src: url('../../../../cover_letters/OpenFonts/fonts/lato/Lato-Lig.ttf'); font-weight: 300; }
@font-face { font-family: 'Lato'; src: url('../../../../cover_letters/OpenFonts/fonts/lato/Lato-Reg.ttf'); font-weight: 400; }
@font-face { font-family: 'Lato'; src: url('../../../../cover_letters/OpenFonts/fonts/lato/Lato-Bol.ttf'); font-weight: 700; }
@font-face { font-family: 'Lato'; src: url('../../../../cover_letters/OpenFonts/fonts/lato/Lato-Bla.ttf'); font-weight: 900; }
@page { size: A4; margin: 0; }
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: 'Lato', Arial, sans-serif; font-size: 10.5pt; color: #222; line-height: 1.45; padding: 22mm 24mm 20mm 24mm; }
/* header, date-line, p, ul/li, closing, signature styles — see worked example */
</style>
</head>
<body>
  <div class="cv-header">
    <div class="cv-name">[YOUR NAME]</div>
    <div class="cv-contact">
      <span><a href="mailto:[EMAIL]">[EMAIL]</a></span>
      <span>[PHONE]</span>
      <span><a href="[LINKEDIN URL]">LinkedIn</a></span>
    </div>
  </div>
  <hr class="header-rule">
  <div class="date-line">[Date]</div>
  <p>Dear [Name/Team],</p>
  <p>[Opening paragraph — role, connection to background, 2-3 sentences]</p>
  <p>[Lead-in to bullets]</p>
  <ul>
    <li><strong>[Label]:</strong> [Concrete achievement/skill]</li>
  </ul>
  <p>[Connection to company — why this role, why this company specifically]</p>
  <p>[Closing line]</p>
  <p class="closing">Kind regards,</p>
  <p class="signature">[YOUR NAME]</p>
</body>
</html>
```

## Tailoring Guidelines

### Salutation
- If you know the hiring manager's name: "Dear [First Last],"
- If you know the team: "Dear [Company] hiring team,"
- Generic: "Dear [Company]," (avoid "To whom it may concern")

### Length - Hard 1-Page Limit
- Target: 1 page including signature
- Maximum: **never exceed 1 page**
- **Word budget: 250-300 words** of body text. 350 words will overflow.
- Opening paragraph + bullet list + closing paragraph = 3 blocks. Add a 4th only if the others are short.
- When adding company-specific content, trim other content to compensate rather than adding net length

### Bullet Lists
- 3-5 bullets is ideal
- Start each bullet with a bold label (`<strong>Label:</strong>`) or action verb
- Every claim backed by a specific, genuine example — no fabricated tools/metrics

### Plain-Text Exception
If the user explicitly asks for a `.txt` cover letter instead of a PDF (has happened for quick/informal applications), write plain text directly with no HTML/compilation step, using the established format (greeting, 2-3 body paragraphs, closing, signature block with email/phone).

### Non-English Cover Letters
- Same HTML structure, just write content in the posting's language
- Adjust date format to local convention
- Adjust closing to local convention (e.g. "Üdvözlettel," for Hungarian)

## Checklist Before Finalizing
- [ ] No em-dashes (use commas or periods instead)
- [ ] No cliches or empty filler
- [ ] Every claim backed by a specific example, no fabricated claims
- [ ] Forward-looking framing: focuses on tasks you'll solve, not just past duties
- [ ] Motivation section references this specific company's mission/values
- [ ] Company name and role are correct throughout
- [ ] Date is current
- [ ] Fits on exactly one page
- [ ] Language matches the job posting language
- [ ] Salutation is appropriate (named person if possible)
- [ ] No Chrome header/footer artifacts in the compiled PDF
- [ ] Fonts render correctly (Lato, not a silent fallback to Arial)

## Submission Guidelines (Best Practice)
- Submit only the documents the employer requests
- Export as PDF (or `.txt` only if the user explicitly asks for that format) to preserve formatting
- Follow all employer instructions regarding anonymity or specific materials
