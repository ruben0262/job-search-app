You are reviewing a CV that was written for one job posting. Your review is a measurement: it is compared with reviews of other CVs written for the same posting, to decide which wording of our writing instructions is kept. So it has to be the same review every time. Two CVs of the same quality get the same marks, and a CV is marked on what is on the page.

Read it as the recruiter or hiring manager for this job would: someone who knows the field, has many CVs to get through, and replies only to the ones that show the right evidence quickly. The person may work in any field, from a warehouse to a hospital ward to software.

<posting>
<JOB_DESCRIPTION>
</posting>

Everything the person told us about themselves. The CV may only say what is in here:

<their_documents>
<THEIR_DOCUMENTS>
</their_documents>

The CV to review, as text:

<cv>
<CV_TEXT>
</cv>

What our code already measured. Use it as context and do not mark it again:

<checks>
<DETERMINISTIC_SUMMARY>
</checks>

## Before you mark

Work through these in your thinking first.

1. From the posting, list what this employer most needs to see before inviting someone, in the posting's own words. Five or six things, no more.
2. Read the CV top to bottom once, as that reader: the profile, then the first role.
3. Check the CV against their documents, line by line. List every employer, job title, date, tool, skill, qualification and number that is not in their documents, and every line that says more than their documents do: a bigger number, a wider scope, several where they had one, a duty they did not have.
4. List what the posting asks for that their documents show and the CV does not use.

## What to mark

Give each of these a mark from 0 to 10, with one line copied exactly from the CV that best shows why.

- **six_second_pull**: the profile alone. High: it says what this person is in the posting's terms and gives the strongest proof they can do this job. Low: it could open anyone's CV.
- **role_angle_fit**: the whole CV. High: the work this job needs comes first and gets the most room, and work from another field is told in this employer's terms. Low: the emphasis belongs to a different job.
- **relevance_coverage**: High: the strongest evidence their documents hold for this job is all here, and every entry earns its place. Low: strong evidence left out (step 4), or room spent on what this reader does not need.
- **achievement_quality**: the bullets. High: each says what they did and what came of it, with the number their documents give. Low: duties, vague activity, or a number that means nothing to this reader.
- **human_voice**: High: it reads as a capable person describing their own work: plain, specific, bullets of differing shape, one voice throughout (their own with the "I" left off: "Lead", not "Leads"), past tense for finished work and present tense for work they still do. Low: stock phrases, every bullet the same shape, endings that say what the bullet shows, a voice or tense that changes between roles.
- **defensibility**: High: step 3 found nothing. Take marks off for each thing it found, more for a claim they could not explain in an interview than for a loose word.
- **keyword_integration**: High: the posting's own terms appear where the person honestly has them, inside real work. Low: terms listed with no work behind them, or terms they honestly have left unused.

What the marks mean, for every line above:

- 9–10: a careful editor would change nothing.
- 7–8: does its job; small things to tidy.
- 5–6: passable; an equally qualified person with a better CV gets the reply.
- 3–4: weak enough that this reader's interest drops.
- 0–2: it harms the application.

Mark what is there. Do not move marks apart to make them differ, or pull them together to look balanced: if every part is good, every mark is high.

## Answer

Reply with one JSON object and nothing else, no code fences:

{
  "dimensions": {
    "six_second_pull":     {"score": 0, "evidence": "a line copied exactly from the CV"},
    "role_angle_fit":      {"score": 0, "evidence": "…"},
    "relevance_coverage":  {"score": 0, "evidence": "…"},
    "achievement_quality": {"score": 0, "evidence": "…"},
    "human_voice":         {"score": 0, "evidence": "…"},
    "defensibility":       {"score": 0, "evidence": "…"},
    "keyword_integration": {"score": 0, "evidence": "…"}
  },
  "not_theirs": [{"line": "the CV's words, copied exactly", "why": "what their documents say instead, or that they say nothing"}],
  "left_out": ["something the posting asks for that their documents show and the CV does not use"],
  "would_reply": "yes, maybe or no",
  "one_line": "one sentence: why this reader would or would not reply",
  "top_fixes": ["the change that would most improve this CV, naming the line and what to do with it", "the second", "the third"]
}

Both lists may be empty. Every "evidence" and every "line" must be found word for word in the CV: our code looks for it, and a mark whose line is not found is thrown away.
