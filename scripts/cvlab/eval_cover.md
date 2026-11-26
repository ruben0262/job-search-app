You are reviewing a cover letter that was written for one job posting. Your review is a measurement: it is compared with reviews of other letters written for the same posting, to decide which wording of our writing instructions is kept. So it has to be the same review every time. Two letters of the same quality get the same marks, and a letter is marked on what is on the page.

Read it as the hiring manager for this job would: someone who knows the work, reads many letters, gives each well under a minute with their own posting in mind, and opens the CV of the ones that answer what the job needs with a real person's work and a true reason for wanting it. The CV holds the detail. The letter is there to answer the posting and to make this reader want the CV. The person may work in any field, from a warehouse to a hospital ward to software.

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

1. From the posting, list what this employer most needs, in the posting's own words, the most important first. Three or four things. For each, look in their documents: do they show work that answers it?
2. Read the letter once, as that reader, start to finish.
3. For each need, find the line where the letter answers it with something they did. A need is answered when this reader can tell, without working it out, that the line is about that part of the job.
4. Check every fact about the person against their documents, and every fact about the company against the posting and the company facts above. List each one that is not there, or says more than they do. Look hardest where the letter speaks of their work in the job's terms: its kind, its size and its setting may be no bigger than their documents give. A firewall set up on one server is not firewall automation, and helping with the orders is not running them.
5. Ask whether the reason given for wanting this job could be said of any other employer.

## What to mark

Give each of these a mark from 0 to 10, with one line copied exactly from the letter that best shows why.

- **opening**: do the first two sentences make this reader want to read on? Their strongest work for this job, and the company and the role named, not "I am writing to apply" or the posting said back.
- **why_here**: is the reason they want this job specific to this company and this work, true to the posting or the company facts, and something this person would plausibly care about? A reason any employer could be given scores low; a flattering line about the company with no link to the person scores low.
- **evidence**: is what the letter says they can do backed by things they did and what came of them, each with enough to be believed and no more? A claim with nothing behind it takes marks off. So does a run of tools, versions, settings or figures that belongs in the CV, and a figure for something this job does not ask about.
- **fit**: does the letter answer what this employer needs most? Each need from step 1 that their documents can answer is met by their work, and this reader can tell which part of the job each paragraph speaks to. A paragraph about them that never touches this job takes marks off. So does the posting's wording said back, its requirements ticked off one by one, or a formula such as "You need X, and I have done X".
- **wants_cv**: having read it, does this reader want to open the CV? The letter makes the case and leaves the detail, and is not their CV again in sentences.
- **voice**: does it read as this person writing, warm, plain and specific, as their own letters do where they gave any, and not as a template or a generated letter?
- **honesty**: is every fact about them in their documents and every fact about the company in the posting or the company facts, each no bigger than the source says, their work said in the job's terms included?
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
    "wants_cv":  {"score": 0, "evidence": "…"},
    "voice":     {"score": 0, "evidence": "…"},
    "honesty":   {"score": 0, "evidence": "…"},
    "close":     {"score": 0, "evidence": "…"}
  },
  "needs": [{"need": "one thing from step 1, in the posting's words", "in_their_record": true, "answered": "yes, partly or no", "line": "the letter's words that answer it, copied exactly, or empty when nothing does"}],
  "not_theirs": [{"line": "the letter's words, copied exactly", "why": "what their documents or the company facts say instead, or that they say nothing"}],
  "left_out": ["something this employer needs that their documents show and the letter does not use"],
  "would_reply": "yes, maybe or no",
  "one_line": "one sentence: why this reader would or would not invite them",
  "top_fixes": ["the change that would most improve this letter, naming the line and what to do with it", "the second", "the third"]
}

"needs" has every need from step 1, three or four, the most important first; "in_their_record" is false where their documents show no work that answers it. The other two lists may be empty. Every "evidence" and every "line" must be found word for word in the letter: our code looks for it, a mark whose line is not found is thrown away, and a need is counted as answered only where its line is found.
