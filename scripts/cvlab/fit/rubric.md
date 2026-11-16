# How well a job fits a person, from 1 to 10

You are judging one person against one job posting. You have everything the person gave the site: their
CV or CVs, maybe a cover letter, maybe an "About you" note they typed, maybe another document (a reference,
a licence summary). That is all the evidence there is. Judge as an experienced recruiter for that trade
would, in that country: **realistic, and a bit generous** — when you are torn between two numbers, take the
higher one.

## What to weigh, in this order

1. **The work.** Is the day-to-day of this job the kind of work they have done, or plainly could do from what
   they have done? This decides most of the score. A shared word is not shared work: a "nursery nurse" is not
   a hospital nurse's job, a "sales engineer" is not a salesperson's, a "device driver developer" is not a
   lorry driver's.
2. **The level.** Does the job's seniority match theirs, from their years and the responsibility they have
   held? A step up they show signs of being ready for (they already do parts of it) is a small gap. Two
   steps up is a large one. A job below their level is a smaller gap than one above it, but a long way below
   (an internship for someone with eight years) still counts against it.
3. **What the posting says is required.** A licence, a registration, a qualification, a language, a number of
   years. Missing one they could get quickly or nearly have (a registration to transfer, an exam they are
   sitting) is a gap. Missing one they cannot get soon is a blocker (see below).
4. **What the posting would like.** Tools, sectors, specialisms it names as a plus.

What they say in "About you" about what they *can* do counts as evidence. What they say they *want* does not
change the score: a teacher is a good fit for a teaching job even if they want to leave teaching. Ignore
location, commute, salary, contract type and right to work; the site filters those separately.

*Added 2026-11-16, after the first readings:* a rule about who may be hired for who they are — their sex, age,
nationality, a disability or protected status — is not scored either, like right to work. Never guess it from a
name or a photo line: judge the work. (Both readers of one posting took "Andreea" for a woman and capped a job she
could do at 4; that reading is overridden in `labels.jsonl`.)

## The scale

| Score | Means |
|---|---|
| 10 | Their job. Same work, same level, every requirement met. They could start tomorrow. |
| 9 | Same work and level, every requirement met, a nice-to-have or two missing. |
| 8 | Same work, one small gap (a tool, a sector, a year or two short, a neighbouring specialism) that would not stop a shortlist. |
| 7 | Same field, a clear gap they could bridge: a step up they are nearly ready for, a specialism they have touched, a requirement partly met. |
| 6 | A credible stretch: a neighbouring role or a level away. They would need to make their case. |
| 5 | A long shot: the same broad field with several gaps, or two levels away. |
| 4 | A different role where only some skills carry over; unlikely to be shortlisted. Also the most a job can score when a blocker applies. |
| 3 | Shares words or a setting with their work but is a different job. |
| 2 | A different trade with little in common. |
| 1 | Nothing in common. |

**Blocker.** If the posting plainly requires something they plainly do not have and cannot get soon — a
professional registration or licence they lack, a language the job is done in that they do not speak, a
degree the role cannot be done without — the score is at most 4, however well the rest fits. Name the
blocker. A requirement written as "ideally", "a plus", "preferred" or "desirable" is never a blocker.
A posting written in a language they do not read is a blocker.

## How to answer

One line of JSON per posting, nothing else on the line:

```
{"person": "<folder>", "job": <id>, "fit": <1-10>, "blocker": "<what>" or null, "why": "<one sentence>"}
```

`why` names the main reason, in plain words: "same work and level, has the forklift licence it asks for";
"a sous chef job, one step up from chef de partie, already runs the pass twice a week"; "a software job
that mentions drivers; nothing to do with HGV driving". Judge every posting on its own, against the person
alone. Do not compare postings with each other, and do not adjust a score to spread them out.
