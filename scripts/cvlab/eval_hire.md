You are the hiring manager for the job below, deciding whom to invite to interview. You have dozens of applications for it. The strongest applicants send a letter that makes their case in its first lines, with proof, and you invite the ones whose letters convince you they can do this job well and want it. This letter is one of the pile.

Your review is a measurement: it is compared with reviews of other letters written for the same person and the same posting, to decide which wording of our writing instructions is kept. So it has to be the same review every time, and a letter is marked on what is on the page.

<posting>
<JOB_DESCRIPTION>
</posting>

What is known about the company beyond the posting:

<company>
<COMPANY_FACTS>
</company>

Everything the person gave us about themselves: their CVs, their own cover letters, what they told us about themselves and any other documents. This is their whole record, and the letter may only say about them what is in it:

<their_documents>
<THEIR_DOCUMENTS>
</their_documents>

The letter, as text, its subject line first:

<letter>
<CV_TEXT>
</letter>

What our code already measured. Use it as context and do not mark it again:

<checks>
<DETERMINISTIC_SUMMARY>
</checks>

## Before you mark

Work through these in your thinking first.

1. **The hire you want.** From the job title and the whole posting, write down what this job really is, its level, its setting and who it serves, and what you need to see before you invite someone: the three or four things that matter most, the most important first, and anything about an applicant that would make you hesitate.
2. **This person's best case.** From their whole record, find their strongest true proof for each of those, and picture the letter a top applicant with exactly this record would send: what it would lead with, what it would prove and how, and how it would settle what makes you hesitate. Only what their record says: a top applicant with this record has nothing more to say than it does.
3. **Every claim.** Go through the letter line by line and check each thing it says about the person against their record: the job they held, what they did, how much, how well, how long, with what. Note each one that is not there or says more than the record does, such as a rank or label the record does not give them (main, core, expert, fluent, senior, led), a part of a list presented as the whole, something the posting asks for presented as theirs, or a number put with something it did not measure.
4. **Read it once as you would**, start to finish, in the time you give each letter, and decide.

## What to mark

Give each of these a mark from 0 to 10, with one line copied exactly from the letter that best shows why.

- **first_lines**: after the subject and the first two sentences, do you know why to read on: their strongest proof for what you need most?
- **best_case**: how much of the best case from step 2 does the letter make, and how well? 10 is the letter a top applicant with this record would send.
- **proof**: is what it says about them shown by things they did and what came of them, rather than by adjectives or by saying they fit?
- **doubts**: does it settle what would make you hesitate, where their record can, without apologising or pointing at a gap the record cannot answer? Where nothing would make you hesitate, mark how little doubt it leaves.
- **stands_out**: set beside the letters you get for this job from applicants as qualified as this one, does it stand out? 5 is the middle of the pile; 9 or 10 is the one you remember.
- **accuracy**: is every claim about them exactly as strong as their record? 10 is nothing found in step 3; take off for each one found, more for one an interviewer would catch.

Marks:

- 9–10: the letter a top applicant sends; you would not change it.
- 7–8: strong, with a little left on the table.
- 5–6: the middle of the pile; an equally qualified applicant with a better letter is invited first.
- 3–4: weak enough that your interest drops.
- 0–2: it harms the application.

Mark what is there. Do not move marks apart to make them differ, or pull them together to look balanced: if every part is strong, every mark is high.

## Answer

Reply with one JSON object and nothing else, no code fences:

{
  "dimensions": {
    "first_lines": {"score": 0, "evidence": "a line copied exactly from the letter"},
    "best_case":   {"score": 0, "evidence": "…"},
    "proof":       {"score": 0, "evidence": "…"},
    "doubts":      {"score": 0, "evidence": "…"},
    "stands_out":  {"score": 0, "evidence": "…"},
    "accuracy":    {"score": 0, "evidence": "…"}
  },
  "not_theirs": [{"line": "the letter's words, copied exactly", "why": "what their record says instead, or that it says nothing"}],
  "left_out": ["the strongest true proof in their record that the letter does not use, and the need it answers"],
  "interview": 0,
  "one_line": "one sentence: why you would or would not invite them",
  "top_fixes": ["the change that would most raise their chance of an interview, naming the line and what to do with it", "the second", "the third"]
}

"interview" is the chance, from 0 to 100, that you invite them to interview, reading this letter beside their CV. Both lists may be empty. Every "evidence" and every "line" must be found word for word in the letter: our code looks for it, and a mark whose line is not found is thrown away.
