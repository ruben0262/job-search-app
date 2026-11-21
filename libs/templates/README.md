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

The writer never sees a template. It writes the middle of the body in the one markup every document
carries, and code writes the rest (`services/docgen/build.py`):

- **CV:** `cv-header`, `cv-name`, `cv-contact` (one `span` a part), `header-rule`, `profile`, `section`,
  `section-title`, `competencies`, `entry`, `entry-head`, `entry-title`, `entry-date`, `entry-desc`,
  `plain-list`. These are the scaffold's fourteen class names, measured on every account's `base_cv.html`
  in 2026-08.
- **Letter:** the CV's header, then `letter-meta` holding `letter-to` (company, place), then
  `letter-subject` ("Application for …"), then `letter`. The company and place are filled from the job by
  code, and `letter-meta` is left out when the job names neither; the subject line is the model's, so it is
  in the letter's language.

**What every document carries, whoever writes it** (owner, 2026-11-08):

- **No date of its own.** Not the day a CV or a letter was made. An AI letter's model is told today's
  date (for counting "Present") and may head the letter with it: `build._undated` takes each line
  that is only a date out of it, and keeps a date inside a sentence.
- **Dates only where the person has them, at the right of the line.** A job with no dates has none, and
  nothing is made of it: no placeholder, no gap, its title where the others are. The reader takes a job
  written without dates as a job of its own (`read._entries`), not as a bullet of the one above.
- **Their links, as links,** in the header of a CV and of a letter: the ones they typed in "Your name
  and links", else the ones their CV has. Each can be followed from the PDF.

Then:

- **A CV or a letter** (`services.docgen.build`, owner 2026-11-10) is written in one call from the person's
  own uploads, by the prompt in `libs/prompts/write_cv.md` or `write_cover.md`, which shows the model the
  markup above and nothing of any look. Code writes the header (the name and links they typed, else their
  CV's, and the email, phone and place their CV has), on a letter the company and its place, and puts the
  look they picked around it. What the model wrote is cleaned to the markup's own tags and class names, so
  a class or style of its own cannot reach the page, and checked for what a reviewer would send back
  (`services/docgen/checks.py`); the ATS check reads the PDF for the CV's own headings, in order, in
  whatever language they are in.
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
decides, not the look: `write_cv.md` asks for it.

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
   pdfminer, did Quill's 0.9pt on 9pt: exactly 0.1 of the size. Less can still split capitals: Kestrel's
   0.8pt on 9pt (0.089) gave "AT TENDANT", "TEL JESKÖRŰ" and "VÉGZET TSÉG" for pdfminer, a pair of narrow
   capitals in its face (2026-11-18), where Foundry's 0.095 reads whole in its own. So it is measured on hard
   pairs in each face (`test_every_look_reads_its_capitals_as_whole_words`), and Kestrel's is now 0.5pt.
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
   - *A real character between the contact line's parts.* Each part but the last ends in a one-point
     space. Five CV looks and the default letter look had only a gap there, which the five readers below
     fill with a space and a reader that takes the characters as they are does not: ResyMatch read a
     phone and an email as one address, "900123maya.example@example.com" (2026-11-14).
   - *A real character before each block, and at each wrapped line's end.* The same kind of reader ran a
     heading into the line under it and a wrapped line's last word into the next line's first
     ("EDUCATIONBSc", "offand"). Each block starts with a space that takes no room on the line, the name
     too, and a wrapped line keeps the space it wrapped at (`break-spaces`, which is why `build.clean`
     leaves no other white space in a document). Over 18 written CVs in each look: 2,538 words run together
     before, none after (2026-11-15). `pre-wrap` was not enough: a justified line still wrote no space
     where it wrapped. With `break-spaces` the space takes its room at the line's end, so a justified
     line ends 2.5 pt short of the edge and now and then a word wraps a line sooner.
9. **Its own colours are listed with it in the catalogue, the main one first** (`accents`), and used in its
   stylesheet for its accents alone, never for its words. A colour picked for both documents (owner,
   2026-11-17) takes every one of them over and nothing else (`recolour`): the main one becomes the colour,
   and a paler or darker one stands to it as it stood to the main. So Ledger draws its name, headings and
   rules in pure black and its words in a near-black, and Foundry's rule shares its bands' slate.
   `test_a_colour_takes_every_accent_of_a_look_and_nothing_else` holds every look to it.

### What is measured, and what is not

Measured on 2026-11-08 with five readers (pypdf, pdfminer, MuPDF, and poppler's `pdftotext` both plain
and `-layout`), over each look's sample, a two-page CV, and a stress CV and letter written as the AI writes
them, with "C++", "C#", ".NET", accents, "Ł" and "€": every word whole, the name first, contact details
exact, sections in order, every font embedded, in all 14 looks and all five readers.

A sixth kind of reader takes a PDF's characters as they are and makes no space of a gap (ResyMatch's,
2026-11-14, `scripts/cvlab/checkers.py`). It ran the contact line's parts together until `EVERY_LOOK` spaced
them. It also ran the last word of a line into the first of the next ("stockdata"), and a heading into the
line under it: Chrome writes no space where a line wraps or a block ends, while LibreOffice keeps one.
Affinda's resume parser, the kind an ATS reads a CV with, ran no two words together in 44 of 44 CVs, so
this was left at first (2026-11-14). It was mended the next day, because ResyMatch counted a posting's word
that the CV has as missing (a nurse's "Patient Care", "Healthcare" and "Clinical"), and a checker a person
may try their CV on should find their words. Where a justified paragraph wraps (Meridian's and Ledger's
profile, 1.8 places a CV) took a second rule that afternoon, when the profile began to name the job and
ResyMatch read "Looking for a WarehouseOperator role": none is left in 144 CVs, and none changed its pages.
`test_no_two_words_run_together_for_a_reader_that_makes_no_space_of_a_gap` holds every look to it, over a
profile of four lines.

**What that parser read** (Affinda, 44 CVs written for eleven made-up people who are not in IT,
`scripts/cvlab/panel`): the name in 44 of 44, the email in 44 of 44, 85% of jobs and qualifications with
their title and employer, and 97% of those with the right dates. Two things it did not read:

- **A phone number after a place.** With the place first in the contact line ("Greater Manchester 07700
  900236") it read no phone at all, 4 of 4. With the place last it read all three, so the header is now
  written phone, email, place, links (`build._header`). A bar between the parts changed nothing.
- **A job with no dates.** Shown as the owner decided, with none (2026-11-08), it is not taken for a job
  of its own: the parser folds it into the one above.

## A few lines on a last page

One line alone on a second page was sent back to the model to cut, a second call of about 45 seconds: 8
of 36 first drafts (2026-11-14). The look takes them up now. `pdf.fitted` draws the whole page slightly
smaller, 98%, then 96%, then 94%, and keeps the first that ends a page sooner; at 94%, 10 pt type prints
at 9.4 pt. What no step takes up is said back as before. Measured over 56 written CVs in each look:

| Look | One page as designed | A few lines spilled | Taken up | Still said back | Two full pages |
|---|---|---|---|---|---|
| Meridian | 48 | 8 | 8 | 0 | 0 |
| Kestrel | 53 | 3 | 3 | 0 | 0 |
| Ledger | 48 | 8 | 8 | 0 | 0 |
| Prism | 25 | 31 | 29 | 2 | 0 |
| Tabula | 31 | 25 | 20 | 5 | 0 |
| Halcyon | 9 | 35 | 10 | 25 | 12 |
| Foundry | 14 | 36 | 21 | 15 | 6 |
| Quill | 13 | 31 | 12 | 19 | 12 |

Each of the 111 taken up read the same to pypdf and pdfminer as before. The five looks at the bottom hold
fewer words on a page than the "about 400" the CV prompt names, which is Meridian's: the same CVs spill
in them five times as often (158 of 280 against 19 of 168).

## What a page of each look holds

The owner, 2026-11-14: tell the prompt the picked look's own number. Each look says how many words of a
CV its page holds below the header (`PAGE_WORDS`, `Template.page_words`), and `build.prepare` hands the
picked look's number to a prompt that asks for it (`{{page_words}}`). Measured over 91 CVs the prompt had
written, each drawn in every look, as its words divided by the share of a page it fills:

| Look | Words a page holds (median, middle half) | Told to the prompt |
|---|---|---|
| Meridian | 420 (403 to 435) | 400 |
| Kestrel | 432 (410 to 448) | 410 |
| Ledger | 415 (400 to 431) | 390 |
| Prism | 376 (358 to 389) | 350 |
| Tabula | 378 (362 to 394) | 360 |
| Halcyon | 331 (316 to 344) | 310 |
| Foundry | 353 (338 to 368) | 330 |
| Quill | 339 (327 to 354) | 320 |

Meridian's is said as the 400 the prompt has always said, and the others in proportion, rounded down to a
ten. `test_each_looks_number_is_in_step_with_what_its_page_holds` fails when a look is redrawn and its
number is not. The live prompt still says 400 to everyone: the wording that asks for the look's number
(`scripts/cvlab/variants/page-words.md`) is measured against it first (CLAUDE.md §3a).

**Measured 2026-11-15, and not adopted.** The same 18 people and postings in Halcyon and in Quill, the two
looks that hold least, on claude-haiku-5-5: told the look's number, a CV was 6.9 ± 4.5 words shorter over
36 pairs, which is no move, and 6 of 36 ran to a second page against 8. The model does not count words as
it writes. What keeps a CV on its page is the fit above and, past that, the say-back. The slot and the
numbers stay for a wording that uses them better.

Not every date: a job's date at the right of its title line, as every look has it and most CVs anywhere
do, is read apart from its title by the two readers that lay text out by position. poppler moves it
after the job's bullets, still in the job; pdfminer gathers some of a section's dates at its end, up to
5 of 15 in a look. pypdf, MuPDF and `pdftotext -layout` keep every one by its title. The date straight
after the title, or under it, kept all of them in place for all five readers. The owner kept the dates
at the right (2026-11-08): the layout recruiters expect, and the one the big parsers read.
