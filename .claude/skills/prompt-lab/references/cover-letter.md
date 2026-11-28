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

1. The opening (owner, 2026-11-28: readers skim to the first paragraph and the last, so those two carry the
   letter) begins with the person, what they like in the work or where their work has been going, in their
   own words; then the job and the company and what they would do there for this employer; then who they
   are, with the one licence the job requires most. Never the posting's duties said back as their wish, never
   a label for a recruiter's client ("we already know that"), the job's title in bold, the company named from
   the job's "Company:" line.
2. One paragraph that makes their case: ONE idea, said first in their own words ("I like finding out why a
   service is slow before deciding what to change"), shown by one or two pieces of work, then one particular
   thing they would do in this job. Two tools at most in the whole letter.
3. A paragraph of its own on why this job, here, at least one reason about the employer itself (from the
   posting or the company note), each said as what the person wants, never the employer told about itself and
   never a figure about it.
4. The close in the owner's shape (2026-11-28): their strongest proof tied to what the job needs, as what they
   are sure they could do there; a confident, general line asking to meet; thanks for the reader's time.
5. It reads as one piece (owner, 2026-11-28: "reads like a top cover letter written by human and it flows
   properly"): one line of thought from why, through what they did, to what they would do; each paragraph
   led in from the one before; no fact standing alone; the practical things (a move, a licence, shifts)
   inside the sentence they belong with, never a sentence or a line of their own. In this person's own
   words for their work, contractions only as their own writing has them. 220 to 280 words, one page.
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
| choppy: short unrelated sentences, a stray fact ("I'm also on call one week in six.") | v39 to v41 ("uneven sentences", "a short one after a long one") | the Flow rule; that wording is gone |
| three results in a row, a stack listed ("on PostgreSQL, Redis and Kafka"): a CV in sentences | v41, worst for developers | "their case is one idea … three results in a row are a CV"; tools counted in the final check |
| a sentence that only announces or points back ("I'm applying for the role that…", "That is what draws me to…") | v37 to v45 | the job named inside the reason; the look's "announces or points back" count |
| the stock bridge "I would bring that same care / attention / approach to…" | 38 of 46 letters of v37 and v41 | "one particular thing they intend to do there … never as 'the same care'" |
| hedged wants ("I'd want to", "I would want to") | v41, with contractions | the Voice rule and the final check |
| the reader told what their own job is ("This role is a backend job inside…", "Your posting says…") | v41 to v45 | the why-here rule; the look's count |
| a paragraph of certificates or the move on its own before the close | v43 to v45 (a sentence that asked for them "together") | "a sentence that closes the paragraph they belong to … never a paragraph of their own" |
| the job placed at the recruiter in the close ("At Adecco I want to…") | v44 | "here or anywhere else in the letter" |
| a label for a recruiter's client ("at a financial services organisation") | v48 (the owner) | the opening rule: no label anywhere |
| the employer's size or reach as the reason ("hundreds of institutions in 40 countries") | every version, Haiku | "no figure about the employer at all" |
| a letter with no close (three paragraphs, then "Kind regards,") | v43, v46, v52, v54, Haiku | `checks._ASKS_TO_MEET` sends it back; the close said last in the prompt |
| the opening as the posting's first duty with "I want to" in front | v49 to v53 (a blind reader: "restates the programme") | the opening begins with the person (v54) |
| "N years of X is what I would bring" in every close | v50 (Haiku, 10 of 11) | "I'm confident that…", never a thing handed over |

## What was learned about judging letters

- **A letter is read beside the one it replaces.** v41 met every target above when read alone. The owner
  read their own v41 letter beside their v37 one: "the v37 read much better", and "I'm applying for the role
  that Radley James is recruiting for at a leading international financial services organisation" was "an
  artificial statement". A fresh reader, blind, agreed on that pair (flow 8 against 4), and across 22 others
  found v41 better for the trades and worse for the developers. `references/blind-read.md`.
- **"More human" is not "less polished".** The feedback "written too correctly or not realistic" was
  answered with contractions, uneven sentences and "as if written in half an hour". The trades' letters
  gained a voice; the developers' letters fell apart into stray facts. What the owner wanted was a letter
  that flows.
- **The model repeats the prompt's own names.** "What they would bring" came back as "What I would bring
  is…" and "what they want to do there" as "What I want to do there is…", past explicit bans, quoted
  examples included. Renaming the parts in the prompt ("their case", "the work they mean to do in this
  job") stopped it; the new name then showed in Haiku's closes ("In this role I would work on…").

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
- **Where an instruction sits matters to Haiku.** It dropped the close or left the company unnamed in about
  1 letter of 11 whatever the middle of the prompt said (v49 to v56), and in none of 19 once those two were
  the last lines before the answer (v57). A rule a model must never miss goes last.
- **One reader's marks drift.** Three blind readers marked the same live letters' closes 5.11, 5.89 and 5.79.
  A split near 10 of 19 is inside one reader's noise; read the reasons, and trust a split like 18 of 19.
- **The company note** (`libs/corpus/companies.py`) holds what a company says of itself in its own postings,
  kept thirty days. Where it is empty the letter has only the posting, and its "why here" is thinner. A
  second source (a search key, or the company's LinkedIn page on the proxy budget) is an open question for
  the owner.

## Known and left, as of v57 (live 2026-11-28, `d885402c2e45`)

- Haiku now and then treats a recruiter as the employer ("take it with Dara People", "Adecco offers both"),
  opens "I am applying for…" (1 of 19), gives a reason about its own life ("Monday to Friday hours suit my
  family"). No check counts these yet.
- Sol opens "I like…" for most people (6 of 8 on the look; v48 7 of 15). The close is the owner's conventional
  one by design, so "Thank you for your time" and "how I could help" are in every letter.
- Haiku writes about 10,000 tokens out a letter and takes about 42 s, 10% more than v48; the prompt is 4,501
  words. Sol about 380 and 12.5 s.
- A letter with no close is sent back in code (`checks._ASKS_TO_MEET`). A colon before a list is a comma
  (`build._colon_as_comma`); a letter without its `<letter>` tags is kept (`build._BARE_LETTER`).
