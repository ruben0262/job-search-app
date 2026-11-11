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

## Before replacing a prompt

`CLAUDE.md` §3a: approved explicitly by the owner, measured with a real A/B, and recorded in
`CHANGELOG.md`. A prompt swapped in on a hunch is the one change on this platform whose damage
shows up as a worse document for a real applicant rather than as an error.
