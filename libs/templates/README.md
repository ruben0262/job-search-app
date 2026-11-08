# templates — how a CV and a cover letter look

**A template is a stylesheet over one markup** (owner, 2026-11-07). Each person picks one look for their
CVs and one for their cover letters in My documents (`User.cv_template`, `User.cover_template`); every
document of that kind is then made in it. None picked is Meridian, the look everyone had before.

| | What it is |
|---|---|
| `__init__.py` | the catalogue: names, sectors, the most popular, `chosen(user, kind)`, `picture(template)` |
| `cv/*.html`, `cover/*.html` | one look each. Only the `<head>` is used: the writer fills the body |
| `../scaffolds/base_cv.html` | Meridian's CV look, read from the scaffold itself and never written (CLAUDE.md §3a) |

## How a document gets its look

The writer never sees a template. It writes the body in the one markup every document carries:

- **CV:** `cv-header`, `cv-name`, `cv-contact` (one `span` a part), `header-rule`, `profile`, `section`,
  `section-title`, `competencies`, `entry`, `entry-head`, `entry-title`, `entry-date`, `entry-desc`,
  `plain-list`. These are the scaffold's fourteen class names, measured on every account's `base_cv.html`
  in 2026-08, and the ones `libs.documents.instant` writes.
- **Letter:** the CV's header, then `letter-meta` holding `letter-to` (company, place), then
  `letter-subject` ("Application for …"), then `letter`. Both are filled from the job when the letter is
  made, never written into a template, and `letter-meta` is left out when the job names neither.

**What every document carries, whoever writes it** (owner, 2026-11-08):

- **No date of its own.** Not the day a CV or a letter was made. An AI letter's model is told today's
  date (for counting "Present") and often heads the letter with it: `build._undated` takes each line
  that is only a date out of it, and keeps a date inside a sentence.
- **Dates only where the person has them, at the right of the line.** A job with no dates has none, and
  nothing is made of it: no placeholder, no gap, its title where the others are. The reader takes a job
  written without dates as a job of its own (`read._entries`), not as a bullet of the one above.
- **Their links, as links,** in the header of a CV and of a letter: the ones they typed in "Your name
  and links", else the ones their CV has. Each can be followed from the PDF.

Then:

- **Instant documents** (`libs.documents.instant`) are written with the template's `<style>` in their head.
- **An AI CV** (`services.docgen.build.tailor`) has its base CV's `<style>` taken off before the call, as
  always, and the *template's* put back after it. A person's `base_cv.html` still decides what the CV
  says. It no longer decides how it looks.
- **An AI letter** (`build.tailor_cover`) is handed the template's `<head>` as `<HEAD_BLOCK>`, which the
  prompt already asks it to use, and the template's `<style>` is put back over whatever came back. The
  prompt does not yet describe the letter frame's class names, so a letter the model writes may place
  its date or subject its own way. Telling it is a prompt change, which waits for the owner (§3a).
- **The picker's pictures** are `templates.picture`, sent with the shelf (`GET /templates`). Each is the
  look in abstract shapes, not a document (owner, 2026-11-07). One small fixed sketch in the markup every
  document carries (a name, a contact strip, headings, a few entries and lines) is laid out by the look's
  own stylesheet, so its colours, bands, rules, type sizes and date column show. Every part is a rounded
  block in its own colour, with no word, letter or character anywhere. The picker shows each look's name
  and nothing else.
- **The site draws each picture in the page**, in a shadow root holding an `<html>` of its own, where the
  look's stylesheet applies exactly as in a page of its own and the site's cannot reach in. Each was a
  frame until 2026-11-08, with a request and a document each: ~40 in a sheet took 1–2 s on a laptop and
  5–10 s as the owner saw them. Drawn in the page, the sheet's 37 are there 0.1–0.26 s after the tap
  (0.4–0.5 s on a CPU slowed fourfold), with no request at all, and pixel for pixel the same as before.

## The catalogue, and why

| Look | CV | Letter | Sectors | What it is |
|---|---|---|---|---|
| **Meridian** | ✓ | ✓ | IT, engineering, business, sales, science, office | Lato, a blue name, ruled headings: the scaffold |
| **Kestrel** | ✓ | ✓ | IT, engineering, business, marketing, science | compact; name and contact on one row; spaced-capital headings |
| **Ledger** | ✓ | ✓ | finance, business, law, government | serif, black only, centred name, ruled headings: the "banking" CV |
| **Prism** | ✓ | ✓ | marketing and design, sales, hospitality | the name in one band of colour; one accent, nothing else |
| **Tabula** | ✓ | ✓ | engineering, finance, government, trades, office (+ healthcare letters) | Carlito, each heading in a pale band; the letter is DIN 5008's, without its date |
| **Halcyon** | ✓ | ✓ | care, teaching, government, sales, hospitality | larger type, more air, a teal bar |
| **Foundry** | ✓ | | trades and logistics, hospitality and retail | heavy plain type, dark heading bars, one page |
| **Quill** | ✓ | | science and research, teaching, law | academic: headings out at the margin over indented sections, a book face |

"Most popular" is fixed from the research (owner: not counted from picks): Meridian, Kestrel, Ledger and
Prism for CVs, and Meridian, Kestrel, Ledger and Tabula for letters. The only split by style found is one
vendor's own platform: clean/modern 42%, professional/classic 31%, creative 18%, minimalist 9%
([StylingCV](https://stylingcv.com/resume-statistics)). Treat it as a hint, not a market figure.
Kickresume's larger dataset (below) agrees on direction but gives no percentages. For letters, Tabula's is
the one most of continental Europe expects.

### What the research found (2026-11-07)

- **One column, plain headings, bold titles over bullets.** In Ladders' 2018 eye-tracking study the CVs
  recruiters spent longest on had simple layouts, clearly marked section and job-title headers and an
  F-pattern of bold titles with bullets under them. The weakest were "cluttered… multiple columns, and very
  little white space" ([HR Dive](https://www.hrdive.com/news/eye-tracking-study-shows-recruiters-look-at-resumes-for-7-seconds/541582/),
  [the study](https://csuglobal.edu/sites/default/files/blog-files/theladders-eyetracking-studyc2.pdf)).
- **What people actually choose.** Kickresume's 2025 data covers 2.1M CVs, ~420K of them downloaded. Its
  most-downloaded templates had "single-column layouts, clear section hierarchy, restrained use of color,
  and minimal decorative elements". 93% call the section "Experience" or a standard synonym
  ([Kickresume](https://www.kickresume.com/en/press/resume-statistics-structure/)).
- **Applicant tracking systems read columns row by row.** A two-column layout is the case that scrambles
  the reading order, and job titles get separated from their dates
  ([Jobscan](https://www.jobscan.co/blog/resume-tables-columns-ats/),
  [benchmark](https://atsverification.com/blog/ats-parsing-benchmark-2026/)). So no look here has columns
  of text side by side (owner, 2026-11-07).
- **Finance and law** expect a conservative serif CV in black: the Harvard career-office order, no
  colour, no graphics ([Wall Street Careers](https://www.wallstreetcareers.com/blog/best-resume-resources-finance-jobs-2026),
  [All About Law](https://www.allaboutlaw.co.uk/law-careers/legal-cv/magic-circle-cv)).
- **Germany and much of Europe** read a tabular CV with the dates in a left column, newest first
  ([how-to-germany](https://www.how-to-germany.com/work/cv/)). Tabula had it, until the owner put every
  look's dates at the right (2026-11-08). The letter has the sender at the top, the
  company in a block on the left, the date on the right and a bold subject with no "Betreff"
  ([karrierebibel, DIN 5008](https://karrierebibel.de/bewerbung-din-5008/)). The French
  lettre de motivation is the same shape, with "Objet" naming the role
  ([jobseeker.com](https://www.jobseeker.com/fr/lettre-de-motivation/conseils/structure-lettre-de-motivation)).
- **The UK:** two pages of A4, no photo, a short profile first
  ([Reed](https://www.reed.co.uk/career-advice/cv-layout-dos-and-donts/)). Letters run your details, the
  date, the recipient and one page ([University of Sheffield](https://sheffield.ac.uk/careers/support/apply/cv/covering-letter)).
- **Nursing and teaching** put the registration (NMC PIN, QTS, DBS) near the top, and a panel scans for
  it ([RCN](https://www.rcn.org.uk/professional-development/your-career/student/student-nurse-cv-writing),
  [nurses.co.uk](https://www.nurses.co.uk/blog/build-your-nurse-cv---step-by-step-guide/)). That is why
  Halcyon uses larger type and bars on its headings.
- **Creative work:** keep the CV parseable and put the design in the portfolio. Use "one font family, one
  accent color maximum", with the portfolio link in the header ([ResumeGeni](https://resumegeni.com/blog/graphic-designer-resume-guide)).
- **Trades, warehouse, hospitality, retail:** one page, licences and tickets easy to find
  ([LiveCareer](https://www.livecareer.co.uk/cv-examples/warehouse-operative-cv)).
- **Academic CVs** are longer, put research or education first and list publications newest first
  ([LSE](https://info.lse.ac.uk/current-students/careers/Assets/Documents/PhD/Academic-CV.pdf),
  [Imperial](https://www.imperial.ac.uk/admin-services/careers/applications-and-interviews/cv/)).

Section *order* (education first for law and academia, registration first for nursing) is what the writer
decides, not the look. It belongs to the CV prompts the owner has scheduled next.

## Adding a look

`tests/test_templates.py` renders every look through the real Chrome, reads it as two kinds of ATS do
(pypdf, in the order Chrome wrote it, and pdfminer, by where each word sits on the page, which is how a
reader finds columns), and holds it to these rules. Every new look broke at least one of them when first
drawn, each looking right on the page:

1. **What an ATS reads is the PDF's text.** It starts with the name, every word comes out whole and
   nothing else does, and every heading is followed by its own section, by both readers.
2. **No floats.** Chrome paints floats before the text around them. Quill's floated margin headings
   reached the text all together, ahead of the name. A grid paints in document order, but see rule 7.
3. **No small capitals, no wide letter-spacing on anything an ATS must read.** `font-variant: small-caps`
   gave "A LEX M ORGAN". 1.4pt of tracking on 9pt gave "E X P E R I E N C E", and so, for poppler and
   pdfminer, did Quill's 0.9pt on 9pt: exactly 0.1 of the size. Keep it under that: the most here is
   Foundry's 0.095, read whole by all five readers measured.
4. **Dates at the right of their line, and nothing drawn left of what precedes it in the markup.**
   Every look puts a job's dates at the right end of its line (owner, 2026-11-08), and the test measures
   it as Chrome prints. Tabula's dates sat left of their titles, the text ran "Northwind LogisticsMar
   2021" until each title ended in a kept space, and an undated title slid into the dates' column.
5. **Every face a look asks for first is installed and checked in `deploy/docker/Dockerfile.docgen`.**
   Chrome falls back without a word, and the test fails on a face the image does not check.
6. **An `@page` rule with `size: A4` and the margins.** Not body padding: that would not repeat on page
   two. The picker pads its pictures by this margin.
7. **Nothing side by side.** A heading or a column of text beside another is read as a column by the
   readers that lay text out by position. Quill's headings sat in a 30 mm margin column and poppler and
   pdfminer read each one apart from its section; on its own line over the section, it reads in place.
8. **What every look keeps is `EVERY_LOOK`** (`libs/templates/__init__.py`), added to the end of each
   look's stylesheet, so a new look cannot leave it out. Don't repeat it in a look:
   - *No ligatures.* Lato and Carlito draw "fi", "fl" and "ffi" as one glyph, which pypdf and pdfminer
     read as one character ("certiﬁed", "oﬃce"): a search for the word does not find it.
   - *The contact line keeps each part whole*, moving it to the next line, and breaks inside one only when
     it is wider than the line (a very long link). Kept whole with nothing to break between them, a long
     contact line's third link went off the page in all thirteen new looks (2026-11-07). Breaking
     anywhere, Meridian put a phone over two lines as "91234-" and "5678", which all five readers read
     wrongly (2026-11-08).

### What is measured, and what is not

Measured on 2026-11-08 with five readers (pypdf, pdfminer, MuPDF, and poppler's `pdftotext` both plain
and `-layout`), over each look's sample, a two-page CV, and a stress CV and letter written as the AI writes
them, with "C++", "C#", ".NET", accents, "Ł" and "€": every word whole, the name first, contact details
exact, sections in order, every font embedded, in all 14 looks and all five readers.

Not every date: a job's date at the right of its title line, as every look has it and most CVs anywhere
do, is read apart from its title by the two readers that lay text out by position. poppler moves it
after the job's bullets, still in the job; pdfminer gathers some of a section's dates at its end, up to
5 of 15 in a look. pypdf, MuPDF and `pdftotext -layout` keep every one by its title. The date straight
after the title, or under it, kept all of them in place for all five readers. The owner kept the dates
at the right (2026-11-08): the layout recruiters expect, and the one the big parsers read.
