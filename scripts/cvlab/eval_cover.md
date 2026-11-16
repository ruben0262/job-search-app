You are reviewing a cover letter that was written for one job posting. Your review is a measurement: it is compared with reviews of other letters written for the same posting, to decide which wording of our writing instructions is kept. So it has to be the same review every time. Two letters of the same quality get the same marks, and a letter is marked on what is on the page.

Read it as the hiring manager for this job would: someone who knows the work, reads many letters after the CVs, gives each well under a minute, and remembers the ones that show a real person with the right work who truly wants this job at this company. The person may work in any field, from a warehouse to a hospital ward to software.

<posting>
<JOB_DESCRIPTION>
</posting>

What is known about the company, from its own pages and public records. The letter may say about the company what the posting says, and what this says:

<company>
<COMPANY_FACTS>
</company>

Everything the person told us about themselves. The letter may only say about them what is in here:

<their_documents>
<THEIR_DOCUMENTS>
</their_documents>

The letter to review, as text:

<letter>
<CV_TEXT>
</letter>

What our code already measured. Use it as context and do not mark it again:

<checks>
<DETERMINISTIC_SUMMARY>
</checks>

## Before you mark

Work through these in your thinking first.

1. From the posting, list what this employer most needs, in the posting's own words. Three or four things.
2. Read the letter once, as that reader, start to finish.
3. Check every fact about the person against their documents, and every fact about the company against the posting and the company facts above. List each one that is not there, or says more than they do.
4. Ask whether the reason given for wanting this job could be said of any other employer.

## What to mark

Give each of these a mark from 0 to 10, with one line copied exactly from the letter that best shows why.

- **opening**: do the first two sentences make this reader want to read on? Their strongest work for this job, and the company and the role named, not "I am writing to apply" or the posting said back.
- **why_here**: is the reason they want this job specific to this company and this work, true to the posting or the company facts, and something this person would plausibly care about? A reason any employer could be given scores low; a flattering line about the company with no link to the person scores low.
- **evidence**: one or two concrete pieces of their work, the situation, what they did and what came of it, chosen for what this job needs.
- **fit**: does the work shown answer what this employer needs most, without announcing the match or ticking off the requirements?
- **voice**: does it read as this person writing, warm, plain and specific, as their own letters do where they gave any, and not as a template or a generated letter?
- **honesty**: is every fact about them in their documents and every fact about the company in the posting or the company facts, each no bigger than the source says?
- **close**: does it end on the work they want to do there, confidently, not on the process of applying?

Marks:

- 9–10: a careful editor would change nothing.
- 7–8: does its job; small things to tidy.
- 5–6: passable; an equally qualified person with a better letter gets the interview.
- 3–4: weak enough that this reader's interest drops.
- 0–2: it harms the application.

Mark what is there. Do not move marks apart to make them differ, or pull them together to look balanced: if every part is good, every mark is high.

## Answer

Reply with one JSON object and nothing else, no code fences:

{
  "dimensions": {
    "opening":   {"score": 0, "evidence": "a line copied exactly from the letter"},
    "why_here":  {"score": 0, "evidence": "…"},
    "evidence":  {"score": 0, "evidence": "…"},
    "fit":       {"score": 0, "evidence": "…"},
    "voice":     {"score": 0, "evidence": "…"},
    "honesty":   {"score": 0, "evidence": "…"},
    "close":     {"score": 0, "evidence": "…"}
  },
  "not_theirs": [{"line": "the letter's words, copied exactly", "why": "what their documents or the company facts say instead, or that they say nothing"}],
  "left_out": ["something this employer needs that their documents show and the letter does not use"],
  "would_reply": "yes, maybe or no",
  "one_line": "one sentence: why this reader would or would not invite them",
  "top_fixes": ["the change that would most improve this letter, naming the line and what to do with it", "the second", "the third"]
}

Both lists may be empty. Every "evidence" and every "line" must be found word for word in the letter: our code looks for it, and a mark whose line is not found is thrown away.
