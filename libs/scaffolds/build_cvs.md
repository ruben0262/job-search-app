You are building CVs for the candidate. Your CLAUDE.md, all skills, and the base
template are loaded. Follow the CLAUDE.md workflow exactly.

For each job below, in order (highest score + newest date first):
1. Create folder cv/jobs/<today's date>/<company-slug>/
2. Fetch the job URL if you need more detail than provided.
3. Write job_context.txt with all application details.
4. Copy the base template cv/base_cv.html to source.html in the job folder
   (one template for every role — decide the angle from the posting).
5. Tailor the CV following CLAUDE.md "CV Content Judgement" rules.
6. Fix @font-face paths: four levels up —
   ../../../../cover_letters/OpenFonts/fonts/lato/...
7. Compile to PDF:
   google-chrome --headless=old --print-to-pdf=cv.pdf --no-sandbox \
     --print-to-pdf-no-header --disable-gpu source.html
8. If the posting requires a cover letter, build cover.html + cover.pdf in the
   same folder.
9. Verify the PDF exists and is exactly 2 pages.

If a single job fails (fetch error, compile error), log it and continue to the
next — do not abort the batch.

After all jobs are processed, output ONLY a JSON array (no other text):
[
  {
    "url": "...",
    "company_slug": "...",
    "cv_path": "cv/jobs/<date>/slug/cv.pdf",
    "cover_letter_path": null or "cv/jobs/<date>/slug/cover.pdf",
    "job_context_path": "cv/jobs/<date>/slug/job_context.txt",
    "success": true/false,
    "error": null or "reason"
  }
]

JOBS TO BUILD CVs FOR:
<PROCESSED_LINKS_JSON_HERE>
