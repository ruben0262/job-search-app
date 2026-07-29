# /setup-role-angles — write the candidate's own role angles

You are writing **one file**: `08-role-angles.md`. It is injected into every CV this person will
ever have built, in place of `<<ROLE_ANGLES>>` in `tailor_cv.md`, and it is the part of tailoring
that cannot be the same for two people.

`tailor_cv.md` knows *how* to write a CV — the six-second test, the bullet formula, ATS strategy,
no fabrication. Those rules are shared and they are not yours to touch. What it cannot know is what
the different kinds of job **this** person chases actually want, and what to lead with for each.
That is this file.

**This command is platform-authored, not ported.** The reference instance had these angles written
inline in its own `tailor_cv.md`, as two lenses — one for engineering roles and one for quant
roles. Correct for one person; wrong for everybody else. A nurse, a solicitor and a data engineer
need three different sets of lenses, and a prompt naming somebody else's is worse than one naming
none.

---

## Prerequisite

`context.md` and `01-candidate-profile.md` must be written. Everything you produce here has to be
grounded in what those say the person has actually done. If they still carry `[FILLED BY` markers,
stop and say so: angles written against an empty profile are invention, and the CV they produce is
one the candidate cannot defend in the interview it wins.

## Step 1 — Read

- `context.md` — the whole thing
- `01-candidate-profile.md` — their history
- `04-job-evaluation.md` — what they count as a good job, which is the best evidence of what they
  are actually aiming at
- `05-cv-templates.md` — how their CV is shaped
- `08-role-angles.md` — the scaffold you are replacing

## Step 2 — Find the angles, do not invent them

Ask yourself: **what genuinely different kinds of job does this person apply for?** Not job titles
— the different things a hiring manager is buying.

Two or three. No more.

- If every job they want is the same kind, **one angle is the honest answer**. Padding it with a
  second invents a career they do not have, and the CV will be aimed at a job they cannot do.
- If you find four or more, you have listed titles rather than angles. Collapse them: two postings
  needing the same evidence surfaced in the same order are one angle.

Where the profile is ambiguous, **ask the user**. One question — "you have both X and Y in here;
are you applying for both kinds of role, or is one of them background?" — is worth more than a
confident guess, and this file is expensive to be wrong about.

## Step 3 — Write each angle

For every angle, four things, and every one of them concrete:

- **When it applies.** How to recognise a posting that wants this *from the responsibilities
  section*, not from the title. Titles lie; what the job actually asks for does not.
- **Lead with.** Which of their experience goes first, and through which lens. Name the actual
  entries from their profile.
- **Condense or cut.** The material that is real but reads as noise to *this* reader. Be specific:
  "cut the pure-research entries", not "cut irrelevant things".
- **Vocabulary.** Insider terms are credibility inside a field and a line the reader skims past
  outside it. Name the terms that help and the terms that hurt, using their real vocabulary.

Then the **adjacent-skill mappings**: postings name tools; this candidate has neighbours of those
tools. Write the mappings that are honestly true — "a posting asking for X is met by their Y work"
— so a real match gets claimed instead of buried. Only mappings a hiring manager would accept if
challenged. A stretch here becomes a lie on a CV.

## Step 4 — Check it against the rule that matters

**Everything in this file must be true of this candidate.** It is not aspiration and not a target
job description. Read it back and ask, line by line: is this describing material that exists in
their profile? Delete anything that is not.

Then write it with `write_profile_file`, and tell the user in **one sentence** what angles you
settled on — in their words, not the file's. They never need to know a file exists.

---

## Format

Keep the shape below. `tailor_cv.md` reads it as prose, so the structure is for the human who opens
it, and short concrete lines beat paragraphs.

```
ANGLE 1 — <kind of role, in plain words>
  When it applies: <what in a posting means this angle>
  Lead with: <which experience, framed how>
  Condense or cut: <what reads as noise to this reader>
  Vocabulary: prefer <terms>; avoid <terms>

ANGLE 2 — <...>
  ...

ADJACENT SKILLS
  A posting asking for <X> is met by their <Y> work.
  ...
```
