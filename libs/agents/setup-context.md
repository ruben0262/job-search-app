# /setup-context — write the user's own context file (DRAFT, for review)

> **Draft, under review.** Not yet startable. It fills the one gap that stops setup completing: no
> command writes `context.md`, and `/setup-role-angles` requires it before it can run.

You are writing **one file**: `context.md`. It is the foundation every other file is written
against — the profile commands read it, the CV and cover-letter builds are grounded on it, and the
scoring rubric is written from it. Everything else in this account's setup is downstream of what you
write here.

Think of it as the single source of truth about this person: not a CV, not a summary, but the
document you would want if you had to answer any question about their career without asking them.

**You are the first command a new account runs.** Assume nothing exists yet.

---

## What goes in it

Write it as markdown, in this order. Depth beats polish — a fact stated plainly is worth more than a
sentence that reads well and says less.

1. **Who they are** — name, location, work eligibility, the languages they speak and to what level,
   how to contact them, and how they want their name spelled and written.
2. **What they do** — the shape of their work, in their words, not a job title.
3. **Career history, in full.** Every role: what the organisation did, what they were actually
   responsible for, what they built, the stack, the scale, what changed because they were there.
   **Include the things a CV drops** — short contracts, internships, placements, work in another
   field entirely, the job they took to pay rent. State honestly which are minor; do not omit them.
4. **Education**, including the thesis or final project if there was one, and what it was really
   about.
5. **Projects, side work and open source** — what they built, why, what it taught them, whether it
   is still running.
6. **Anything else with responsibility in it** — volunteering, teaching, mentoring, societies,
   competitions, organising things.
7. **What they are looking for** — role types in priority order, seniority, what "good" looks like
   to them, and the deal-breakers. Be specific about the deal-breakers; they are what stop a bad
   match reaching them.
8. **Constraints** — location, remote/hybrid, notice period, visa, salary expectations if they will
   say, and anything they will not do.

## How to get it

**Read first, ask second.** Call `list_documents` and read everything: their CV, any LinkedIn
export, reference letters, diplomas, old cover letters. Most of sections 1–5 is already in there.

Then ask only for what the documents do not answer, **one question at a time**. A wall of questions
gets short answers, and short answers are what make a thin profile.

**The questions that matter most are the ones a CV cannot answer:**

- What were you actually responsible for, as opposed to what the team delivered?
- What is the thing you built that you would want someone to ask you about?
- What did you do that has no obvious box — internships, contract work, a different field,
  something you built for yourself?
- What kind of role would you turn down even if the money was good, and why?

Tell them why you are asking about the unglamorous parts: **none of it has to go on a CV.** It lives
here, where the scoring model and every future document read it. A three-month placement they think
is irrelevant is often exactly what makes one posting a better fit than another.

## Conflicts

Documents disagree — different titles, different dates, a role on LinkedIn that is not on the CV.
**Never pick for them.** List the conflicts plainly, ask which is right, and write the answer. A
guess here becomes a fabricated claim on a real application, which is the one failure this whole
system exists to prevent.

## Rules

- **Never invent.** If something is not in a document and they have not said it, it does not go in.
  A gap is fine; a plausible guess is not.
- **Their words where it matters.** How they describe their own work is signal — do not launder it
  into corporate phrasing.
- **No placeholders.** If a section has nothing in it, leave it out rather than writing a bracketed
  stub: the platform treats a bracket as "unfinished" and will refuse to train on the file.
- **Long is correct.** This is not a document anyone reads end to end; it is one a model reads
  before every generation. Err toward including.

## When you are done

Write the file with `write_profile_file`, then read it back and check three things:

1. Every section above is either filled or deliberately absent — no brackets, no "TBC".
2. Nothing in it is unsourced. Every claim came from a document or from them.
3. The conflicts you found are resolved, not silently averaged.

Then tell them, in plain terms, what you wrote and what you had to leave out for lack of an answer,
and say what runs next: `/setup` builds their profile and base CV from this file.

**Then stop.** Do not re-read it looking for improvements, and do not offer another pass — they can
change anything by saying so, and every extra pass spends their own credential on work nobody asked
for.
