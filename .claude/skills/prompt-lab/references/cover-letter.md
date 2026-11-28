# The cover letter: what it is read against, and how it got there

The live prompt is `libs/prompts/write_cover.md`; every earlier one and every draft not adopted is in
`libs/scaffolds/history/` with a row in its README. Numbers and dates are in `CHANGELOG.md`. This file keeps
what a future session needs in order to read a letter the same way.

## What a cover letter is for (owner, 2026-11-27)

"A cover letter is supposed to be like a motivation letter": it sells the person to the employer by
answering **why this employer, why this work, why them**. The CV proves what they did. A letter that walks
through the posting's skills is a second CV, and it was what ours wrote until v37.

Research behind it (careers services, hiring-manager guidance, published strong letters): the letters that
work open on a reason that fits no other job, give one or two things the person would bring, each shown by
one piece of work, say something true and particular about the employer joined to what the person wants,
and close by asking to talk. They are short, sound like one person, and leave the detail to the CV.

## Targets (read every letter against each)

1. The opening says why they want this job, in a way no other job would fit, then one fact about them.
   The company as people say it, the job's title in bold, as it reads in a sentence.
2. One paragraph on what they would bring: two or three things, each shown by one thing they did. Two tools
   at most in the whole letter.
3. A paragraph of its own on why this job, here: one or two true things about the employer (from the
   posting or the company note), each joined to what they want or where their work has been heading.
4. A close on what they want to do there, then one plain line asking to talk about one named thing.
5. It reads as this person wrote it in half an hour: their own phrases, contractions where they would use
   them, uneven sentences. 220 to 280 words, one page.
6. Every fact about them exactly as strong as their record. The reason for wanting the job may be reasoned
   out ("a logical and calculated inference", the one "that sells the user best"); a fact may not.

## Mistakes that must not come back

| mistake | first seen | what stops it now |
|---|---|---|
| the letter is the CV in sentences: tools, versions, figures | v20 and before | targets 2 and 3; "two tools at most" |
| the posting's requirements ticked off, or said back ("You need X, I have done X") | v23 to v32 | the prompt's never-list; read for it |
| a company and address block, a subject line that claimed a speciality | v20 | removed from the markup and from `build.py` |
| a recruiter's posting answered as a job at the recruiter | v23 | "the … role that Radley James is recruiting for" |
| a gap or weakness named ("I have not yet…", "phone calls take me longer") | v35, v40 | the never-list ("or find hard"); read for harm |
| a wish the job cannot meet (another city, other hours) | v35 | the never-list; check the posting's place and hours |
| something the posting asks for claimed as theirs (Go, for a Python developer) | the trap, every version | read the trap's letters first; `CLAUDE.md` §3b gate 10 |
| the board's punctuation in the title ("Software Engineer – Python & Infrastructure Automation") | v37 | `build.in_a_sentence`, in code, for every writer |
| the prompt's own names for the paragraphs ("What I would bring is…", "What I'd bring is…") | v37 to v40 | the Voice rule; the look's "the prompt's part names" count |
| "written too correctly": no contractions, balanced sentences, threes | v37, v38 (a reader's word) | the Voice rule; `checks.letter` no longer sends a contraction back |
| one writer's phrase in most people's letters ("What draws me to…", Sol) | 2026-11-19, again v39 | the never-list; `formulas.py` on every look |
| a close with no ask, because the checks sent "I would welcome a conversation" back | until v37 | `checks.LETTER_PHRASES` keeps only the stock forms |

## What was learned about judging letters

- **The reviewers reward what they were written for.** `eval_cover.md` and `eval_hire.md` count the
  posting's needs answered. They marked the skills-proof letters high and would mark a motivation letter
  down. They are kept for the paired bench and must be rewritten against the targets above before they are
  used on a motivation letter. Until then the judgement is the session's reading.
- **Six content wordings (v24 to v31) did not beat v23 on pairs they were not picked on.** The one that
  looked best on its tuning pairs (v27, +2.8 ± 1.0 of 100) was −0.5 ± 1.3 on 27 unseen pairs. A change that
  small is not found by tuning on thirty pairs. The changes that held were changes of concept (v23, v37)
  and fixes of a habit that could be seen in the letters (v32, v38 to v41).
- **Each fix moves something else.** Stopping "What draws me to" made every Sol letter open with "I want
  to"; letting contractions in made Haiku say "What I'd bring is…" past a rule that named only "What I
  would bring is…". After a change, run `formulas.py` and read the openings and the first words of each
  paragraph across all the letters, not one letter at a time.
- **A person's own words can harm them.** "Their own words where those are plain and true" pulled in a
  payroll clerk's honest line about slow phone calls in English, for a job in English. True is not enough:
  the letter says what they bring.
- **The company note** (`libs/corpus/companies.py`) holds what a company says of itself in its own postings,
  kept thirty days. Where it is empty the letter has only the posting, and its "why here" is thinner. A
  second source (a search key, or the company's LinkedIn page on the proxy budget) is an open question for
  the owner.

## Known and left, as of v41 (live 2026-11-27, `3fdee8a2cdb4`)

- Most letters open "I want to…" (Sol 6 to 8 of 8 people, Haiku 5 to 6). It is plain and it is the
  design, and it is one writer's phrase at the scale of a site. The next wording step, if the owner wants
  one, is the opening said the way each person would start it.
- Haiku writes 7,000 to 9,000 tokens out a letter (thinking included) and takes 30 to 40 s; Sol 360 and 11 s.
- Haiku's first drafts are sent back for a colon in a sentence about one time in ten (v35 to v41). v39 and v41
  each put a sentence with a colon of its own into the prompt; whether that teaches it has not been tested.
- One Haiku call in about fifty returned no complete letter. Production retries; the cause was not found.
