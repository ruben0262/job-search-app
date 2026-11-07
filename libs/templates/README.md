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
- **Letter:** the CV's header, then `letter-meta` holding `letter-to` (company, place) and `letter-date`,
  then `letter-subject` ("Application for …"), then `letter`. All three are filled from the job on the
  day the letter is made, never written into a template.

Then:

- **Instant documents** (`libs.documents.instant`) are written with the template's `<style>` in their head.
- **An AI CV** (`services.docgen.build.tailor`) has its base CV's `<style>` taken off before the call, as
  always, and the *template's* put back after it. A person's `base_cv.html` still decides what the CV
  says. It no longer decides how it looks.
- **An AI letter** (`build.tailor_cover`) is handed the template's `<head>` as `<HEAD_BLOCK>`, which the
  prompt already asks it to use, and the template's `<style>` is put back over whatever came back. The
  prompt does not yet describe the letter frame's class names, so a letter the model writes may place
  its date or subject its own way. Telling it is a prompt change, which waits for the owner (§3a).
- **The picker's pictures** (`GET /templates/{kind}/{id}/preview`) are `templates.picture`. Each is the
  look in abstract shapes, not a document (owner, 2026-11-07). One small fixed sketch in the markup every
  document carries (a name, a contact strip, headings, a few entries and lines) is laid out by the look's
  own stylesheet, so its colours, bands, rules, type sizes and date column show. Every part is a rounded
  block in its own colour, with no word, letter or character anywhere. The picker shows each look's name
  and nothing else.

## The catalogue, and why

| Look | CV | Letter | Sectors | What it is |
|---|---|---|---|---|
| **Meridian** | ✓ | ✓ | IT, engineering, business, sales, science, office | Lato, a blue name, ruled headings: the scaffold |
| **Kestrel** | ✓ | ✓ | IT, engineering, business, marketing, science | compact; name and contact on one row; spaced-capital headings |
| **Ledger** | ✓ | ✓ | finance, business, law, government | serif, black only, centred name, ruled headings: the "banking" CV |
| **Prism** | ✓ | ✓ | marketing and design, sales, hospitality | the name in one band of colour; one accent, nothing else |
| **Tabula** | ✓ | ✓ | engineering, finance, government, trades, office (+ healthcare letters) | dates in a left column; the letter is DIN 5008's |
| **Halcyon** | ✓ | ✓ | care, teaching, government, sales, hospitality | larger type, more air, a teal bar |
| **Foundry** | ✓ | | trades and logistics, hospitality and retail | heavy plain type, dark heading bars, one page |
| **Quill** | ✓ | | science and research, teaching, law | academic: headings in the margin, a book face |

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
  ([how-to-germany](https://www.how-to-germany.com/work/cv/)). The letter has the sender at the top, the
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

`tests/test_templates.py` renders every look through the real Chrome and holds it to these rules. Every
new look broke at least one of them when first drawn, each looking right on the page:

1. **What an ATS reads is the PDF's text, in the order Chrome wrote it.** It starts with the name, and
   every heading is followed by its own section.
2. **No floats.** Chrome paints floats before the text around them. Quill's floated margin headings
   reached the text all together, ahead of the name. Use a grid, which paints in document order.
3. **No small capitals, no wide letter-spacing on anything an ATS must read.** `font-variant: small-caps`
   gave "A LEX M ORGAN". 1.4pt of tracking on 9pt gave "E X P E R I E N C E". Keep tracking at or under
   ~0.1 of the font size, as every look here does.
4. **Something drawn left of what precedes it in the markup needs a space kept between them.** Tabula's
   date sits left of its title, and the text ran "Northwind LogisticsMar 2021" until each title ended in
   a kept space.
5. **Every face a look asks for first is installed and checked in `deploy/docker/Dockerfile.docgen`.**
   Chrome falls back without a word, and the test fails on a face the image does not check.
6. **An `@page` rule with `size: A4` and the margins.** Not body padding: that would not repeat on page
   two. The picker pads its pictures by this margin.
7. **The contact line wraps** between its parts (`display: flex; flex-wrap: wrap`), and inside a part
   wider than the line (`overflow-wrap: anywhere`). Each part kept whole, with nothing to break between
   them, put the third link of a long contact line off the page: missing from the PDF in all thirteen new
   looks. A first version of the pictures, which drew each word of a sample, showed it.
