# Superseded prompts

Every version of a scaffold prompt that was live and has since been replaced, named
`<prompt>.<date>.<what-replaced-it>.md`.

Git holds this history too. This directory exists because a prompt is not read like code: when a
letter comes out wrong six months from now, the question is "what were we asking for at the time",
and that should be answerable by opening a file rather than by bisecting. `CHANGELOG.md` carries
the reasoning and the numbers; these are the artifacts those numbers were measured against.

**Nothing reads this directory.** It is not a fallback, not a rollback mechanism and not on any
import path. Restoring one is a deliberate copy, done by a person who has read why it was replaced.

## What is here

| file | was live until | replaced by | why |
|---|---|---|---|
| `tailor_cover.2026-08-09.pre-H.md` | 2026-08-09 | variant H | +8.4 judged points over 16 paired letters (t=4.19); see `CHANGELOG.md` |
| `tailor_cover.2026-08-09.pre-cv-block-removal.md` | 2026-08-09 | H minus the `<CV_HTML>` block | CV lifting fell from 5 letters in 8 to 1 (t=-2.38); judged score unchanged; see `CHANGELOG.md` |
| `tailor_cv.cvlab-d-both.md` | never live: a CV-bench draft of `tailor_cv.md` | `libs/prompts/write_cv.md` (2026-11-11) | kept for its rules (owner, 2026-11-11): no first person, and what to do when relevant experience runs out |
| `tailor_cv.cvlab-d-fallback.md` | never live: a CV-bench draft | `write_cv.md` (2026-11-11) | its "not enough directly relevant experience" rule is carried into `write_cv.md` |
| `tailor_cv.cvlab-d-person.md` | never live: a CV-bench draft | `write_cv.md` (2026-11-11) | its first-person rule is carried into `write_cv.md` and `services/docgen/checks.py` |
| `eval_cv.2026-11-14.pre-any-field.md` | 2026-11-14 (the CV bench's reviewer, `libs/prompts/eval_cv.md`) | a reviewer for any field that is given the person's documents | written about one person in tech, and it never saw the documents: a skill and a duty planted in four CVs cost 0.50 ± 0.20 of its honesty mark, and 4.62 ± 0.55 of the new one's, which listed all 16; see `CHANGELOG.md` |
| `write_cv.2026-11-15.pre-v12.md` | 2026-11-15 (version `01faad6c1265`) | bench draft v12, now `libs/prompts/write_cv.md` (version `6fcbbabfca70`) | read crossed over 18 pairs, v8 scored +2.5 ± 0.8 of 100 over it (voice +1.06 ± 0.25, said that is not theirs 0.67 a CV against 1.61, left out 2.61 against 3.44); v12 is v8 and one sentence, and passes 99.3 of 100 ATS checks where this passed 86.8; see `CHANGELOG.md` 2026-11-15 |
| `write_cv.cvlab-v5.md` | never live: a CV-bench draft, 2026-11-14 | v8 | one voice in profile and bullets, "say no more than their documents do", skills by category, tense by what is finished: all carried into v12. On its own it left 0.56 ± 0.20 more evidence out a CV |
| `write_cv.cvlab-page-words.md` | never live: the live prompt with the picked look's own page number, 2026-11-14 | the `{{page_words}}` slot in v12 | measured alone it moved no length (−6.9 ± 4.5 words over 36 pairs); the slot is carried into v12 |
| `write_cv.cvlab-v6.md` | never live, 2026-11-15 | v7 | its rule that evidence the posting asks for gets a bullet is carried into v12. Dropped: `"Class 1" for a C+E licence` (a category C licence written up as Class 1), "the kind of job they want next" (a supervisor's job named on an advisor's CV), "go down your list once more" (3 of 18 CVs over 16,000 tokens) |
| `write_cv.cvlab-v7.md` | never live, 2026-11-15 | v8 | job dates in one form and "a bullet's unsupported ending comes off" are carried into v12. Dropped: "results before duties" (a nurse's job opened on an audit) and a day's date written out (sent back by our own number check) |
| `write_cv.cvlab-v8.md` | never live, 2026-11-15; the owner's "v8 looks good" | v12 (v8 and one sentence) | the wording read crossed against the live one; everything in it is in v12 |
| `write_cv.cvlab-v9.md` | never live, 2026-11-15 | nothing | a second page for a career over ten years: one two-page CV of ten, a few lines on its second page, two CVs over 20,000 tokens |
| `write_cv.cvlab-v10.md` | never live, 2026-11-15 | nothing | up to fifteen bullets: not shown better (read by other reviewers than v8, not crossed) |
| `write_cv.cvlab-v11.md` | never live, 2026-11-15 | nothing | v8 and four sentences on the profile, long careers and tool lists: +0.00 ± 1.32 against v8, read crossed |
| `write_cv.cvlab-v13.md` | never live, 2026-11-15 | nothing | "say each thing once": repetition 13.1 to 2.2 runs a CV, and 28 words shorter, the profile's proof gone, the reviewer's score −2.1 ± 1.2 |
| `write_cv.cvlab-v14.md` | never live, 2026-11-15 | nothing | Skills to carry every posting term their documents show: no keyword count moved, and said that is not theirs 0.56 to 1.17 a CV |
| `write_cv.cvlab-no-final-check.md` | never live, 2026-11-15 | nothing | the live wording (v12) without its final self-check, to see what the check costs: no time or tokens saved (−1.1 ± 3.2 s, −265 ± 825 tokens out), and 3 of 18 CVs sent back by our own checks where none was |

## Before replacing a prompt

`CLAUDE.md` §3a: approved explicitly by the owner, measured with a real A/B, and recorded in
`CHANGELOG.md`. A prompt swapped in on a hunch is the one change on this platform whose damage
shows up as a worse document for a real applicant rather than as an error.
