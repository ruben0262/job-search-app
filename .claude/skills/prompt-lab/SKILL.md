---
name: prompt-lab
description: How a prompt in this repository is researched, changed, tested and judged, start to finish, without shipping a worse document to a real person. Use it for ANY work on what the AI writes here - the cover letter prompt, the CV prompt, the help chat, anything under libs/prompts, libs/scaffolds or scripts/cvlab/variants - and whenever the owner says a letter or CV "reads wrong", "sounds like AI", "is too stiff", "talks about the wrong things", asks to "improve", "tune", "evolve", "test" or "evaluate" a prompt or wording, wants a new kind of document written, or asks which model writes best. Use it before proposing a single reworded line, because the first step is never the wording.
---

# Prompt lab

A prompt here writes what a real person sends to an employer. A worse prompt does not raise an error. It
shows up as a worse letter, weeks later, as a job somebody did not get. So a wording is changed the way
this file says, and **nothing reaches `libs/prompts/` without the owner's yes to the result** (`CLAUDE.md`
§3a; leave to try a wording is not leave to ship it).

The method below is what worked on the cover letter on 2026-11-26 to 28, after two things that did not: six
wordings tuned and measured carefully against the wrong idea of what a cover letter is for, and then a
wording (v41) that passed every target when its letters were read one by one, went live, and read worse than
the one it replaced when the owner set their own two letters side by side. Read `references/cover-letter.md`
for that case in full, including the targets and the mistakes list a letter is still read against, and
`references/blind-read.md` before any decision between two wordings. For the CV or any other prompt the
steps are the same and the lists are written anew.

## The order, and why it is this order

1. **Find out what the document is for.** Before reading the prompt, research how this kind of document
   works on its reader: what guidance for hiring managers and careers services says, what strong real
   examples do, what readers say puts them off. Reverse it into a short list of **targets** (what a good one
   does, each checkable by reading) and a list of **mistakes** (what ours did wrong before, each of which must
   not come back). Say them to the owner in plain words and get agreement before any wording work: the owner
   knows what they want when they see it named ("a cover letter is a motivation letter"), and one sentence
   from them here saves a day of tuning.
   *Why first:* a measured improvement toward the wrong target is still wrong, and reviewers and checks
   reward whatever they were written for.

2. **Find the gap, and say whose it is.** Write the live prompt's documents for the look (below) and read
   them against the targets. For each miss decide where it lives:
   - **the shape** the prompt asks for (then it is a wording change);
   - **an input the prompt never gets** (the company's own words about itself: built by rule from its
     postings and stored, `libs/corpus/companies.py`, never asked of the model);
   - **something code can do** (a title's board punctuation: `build.in_a_sentence`, after the model, for
     every writer). `CLAUDE.md` §0: rules before AI. A fix in code holds for every model and costs no tokens;
   - **a check that punishes what is now wanted** (next step).

3. **Fix the judging before testing anything.** Read every rule that can send a document back
   (`services/docgen/checks.py`) and every reviewer rubric (`scripts/cvlab/eval_*.md`) against the new
   targets. A check that sends back what the target asks for costs the person a second call and teaches the
   prompt the old habit: the close that asks to talk was being sent back, and so were the contractions a
   person writes. Change those first, with a test, or the round measures the old concept.

4. **Write with the models a person writes with, as production calls them.** `CLAUDE.md` §3b gate 6:
   Claude Haiku 5.5 at `build.EFFORT` and ChatGPT's Sol, lean. A wording read on one writer has not been
   read on the other: each has its own habits (Sol's "What draws me to…", Haiku repeating the prompt's own
   paragraph names).
   - `scripts/cvlab/look.sh <look> <variant> haiku` uses this machine's own Claude Code sign-in
     (`run.py --own-claude`): the identical production call, on nobody's linked account.
   - `scripts/cvlab/look.sh <look> <variant> sol` uses the linked ChatGPT sign-in (needs the local mongod).
   - Other models (Sonnet, Opus, Haiku at high effort) only when the question is which model writes best.

5. **One change at a time, as a variant, and a phrase taken out before a ban is added.** A model repeats
   the prompt's own words: a paragraph the prompt called "what they would bring" came back as "What I would
   bring is…", past three bans of the phrase, and stopped when the part was renamed. Look for the cause in
   the wording before writing "never". A candidate is `scripts/cvlab/variants/<kind>/vN.md`: the
   previous one plus one named change, a sentence or two. Log each in a `progress` file in the working
   folder (what changed, why, what the read found), because a session's context is summarised and the log
   is what survives. Prefer replacing a sentence to adding one: every candidate so far got longer, and long
   prompts are followed less evenly.

6. **Judge by reading, here, in the session, and always beside the wording it would replace.** This is the
   evaluation (owner, 2026-11-27: "evaluation falls on you only with a couple of profile examples"). No
   reviewer model, no scoring rig, unless the owner asks for the full gates. Two reads, both needed:
   - **yours, against the targets and the record** ("Reading a look" below): the facts, the trap, harm;
   - **a fresh reader's, blind, side by side** (`references/blind-read.md`): which wording's letter for the
     same person reads better. You wrote the candidate and know which letter is which, so the choice between
     two wordings is not yours to make by eye. One agent, one brief, the sets coded by `look.py blind`.

7. **Decide on what recurs or harms, and on the blind ranking.** A candidate is offered only when the fresh
   reader ranks it at or above the live wording in most sets on **both** writers, and above it on any pair
   the owner complained about. Model output varies run to run. A flaw in one letter of twenty-two
   is noise unless it harms the person (a weakness named, a fact not theirs). A flaw in three of eleven is
   the prompt. Change the prompt for the second kind and for harm; leave the rest. And say what got worse:
   each change so far fixed its target and moved something else (stopping "What draws me to" made every Sol
   letter open "I want to").

8. **Stop, report, ask.** Report the table, two or three documents whole, what was run and **what was not
   run** (a gate not run is never reported as passed), and ask the owner for a yes or no on the named
   version. Then "Going live" below.

## Reading a look

A look is a small fixed set of people and postings, `scripts/cvlab/looks/<name>.json`: for letters, eight
people in different trades with weak letters of their own, a posting a recruiter wrote for a client, and the
trap (a developer with Python and no Go, for postings that ask for Go). `look.sh` writes the documents and
prints one line a document, then every document whole.

**Check the first paid document before leaving the run** (`CLAUDE.md` §3b, last paragraph): the log's
`first document:` line against the tokens kept for that writer, and that the writer is the one meant.

Then read **every** document, start to finish, as the person it is sent to would. For each one:

- **The targets**, one by one. Not "is it good" but "does the opening say why they want it", "is there a
  paragraph about why here".
- **Every fact, traced.** For each number, employer, date, certificate and claim about the person, find it
  in their record (`scripts/cvlab/panel/<person>/*.txt`, and their letters overlay); for each thing said
  about the employer, find it in the posting or the company note. Use grep: do not trust the read. Records
  are in the person's own language, so search in it ("telefon", not "phone").
- **The trap.** Nothing the posting asks for is claimed as theirs. Read the trap's letters first.
- **Harm.** A weakness named, a gap raised, a wish the job cannot meet, a complaint about an employer.
- **Flow.** Does each sentence follow from the one before, does each paragraph have one idea and say it
  first, does the letter read as one piece? Three results in a row are a CV. A fact standing alone, a
  sentence that only announces the application or points back at the role, a paragraph of certificates
  before the close: each is a break a reader feels before they can name it.
- **Voice.** Would this person have written it? It follows the person: contractions where their own writing
  has them, words in full where it does not. "Written too correctly" (a reader's word, 2026-11-27) was
  answered with uneven sentences and "as if written in half an hour", and that made letters choppy without
  making them anyone's. What makes a letter theirs is their own words for their work and for what they want.
- **The same words in different people's letters**: `python scripts/cvlab/formulas.py <writer folder>`.
  A phrase one writer gives five of eight people is the model's, and a recruiter learns it.

The line a document shows what code can count: the look's own counts (a `!` marks one to read first), how
many first drafts production's checks sent back and why (each is a second call on the person's plan), hits
on the AI-language list, pages, the `/no-ai-language` checker's hard hits ("not run" where that skill is not
on the machine), tokens out and seconds. **Counts are a reading aid, never the judgement**: the pattern for
"opens on why" misses "I like it when a room is perfect", which is a fine opening.

The filters every candidate passes before it is offered to the owner:

| filter | how | passes when |
|---|---|---|
| production checks | the line's "sent back" | no more first drafts sent back than the live wording |
| AI language | the line's "AI list" and "reads as AI" | no hit; every hard hit read, and either the person's own word or fixed |
| one page | the line's "pages" | 1 for a letter |
| ATS, every look | `look.sh <look> <variant> <writer> --dry-run --every-look` | every check in all six looks |
| facts | the trace above | nothing the record does not hold, nothing bigger than it says |
| cost | tokens out and seconds against the live wording's | no more, or it says what the extra bought |
| beside the live wording | `look.py blind`, one fresh reader, `look.py unblind` | at or above the live wording in most sets, on each writer |
| both writers | all of the above on haiku and on sol | both |

## What a few documents cannot tell you

- **A small difference.** Eleven letters can show that a habit is there or gone, and a blind ranking of
  twenty-five sets can tell a wording that reads better in most of them. Neither can rank two wordings that
  are close (v44 and v45: 11 sets to 14). If that question matters, it needs the paired bench (`run.py`
  with `--judge`, both reviewers, several samples), and the reviewers' rubrics checked against the targets
  first.
- **Your own eye on your own wording.** It found v41 good, and v44 better than v45 on Sol. The owner and
  the blind reader found otherwise both times.
- **People it was tuned on.** A wording picked because it read best on these pairs will read worse on
  others: v27 scored +2.8 ± 1.0 on the 30 pairs it was picked on and −0.5 ± 1.3 on 27 it had never seen.
  Before going live, read a few people the wording was never changed for (`--candidates` with others from
  `scripts/cvlab/panel`), and say in the report whether that was done.
- **Other languages.** Letters are judged in English for now (`CLAUDE.md` §3b gate 1).

## Spending the plans

- `--own-claude` spends this session's own plan and its usage cannot be read from here: a look is eleven
  calls a writer; keep a round to a look or two, and stop at the first "hit your" in a log.
- Sol spends the ChatGPT plan the owner also tests the site with. One look a candidate, not five.
- One process for each stored sign-in at a time. Never read a credentials file to check usage.
- A document already kept is not written again: the same command fills only what is missing.

## Going live (only on the owner's yes to a named version)

1. `libs/scaffolds/history/`: the live prompt copied in as `<file>.pre-vN.md`, every draft not adopted
   moved in, each with a row in its README saying what it tried and what the read found.
2. The chosen variant copied to `libs/prompts/<file>.md` byte for byte; `scripts/cvlab/variants/<kind>/`
   left with only `.gitkeep`.
3. `CHANGELOG.md`: what changed and why in the owner's words, the table, what was read by whom, and what was
   **not** run. `docs/saas/ROADMAP.md` where the plan changed.
4. `scripts/gates.sh`; commit on main with CHANGELOG and ROADMAP whole; never push (a push is a deploy, and
   it is the owner's).

## For a prompt that is not the cover letter

The same eight steps. What has to be made first, and has not been yet:

- **Its look**: `scripts/cvlab/looks/<name>.json` with `kind`, the pairs, and counts written from *its*
  targets. For the CV: people whose uploads are thin, long, in two columns, in another language; the trap.
- **Its targets and mistakes**, researched for that document (a CV is read by a parser and then by a person
  in six seconds, a help-chat answer by somebody stuck on a screen).
- **Its filters**: for a CV `checks.cv`, invented numbers, and ATS in every look carry more weight than
  voice; for the help chat there is no bench yet, and saying so is part of the report.
