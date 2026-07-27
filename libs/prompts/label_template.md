You are scoring job postings for <<NAME>> to build a TRAINING dataset, so CONSISTENCY
is the priority: a job of a given quality must get the same score no matter what other
jobs are in the batch. Score each job INDEPENDENTLY against this rubric, never relative
to the rest of the batch. Score from ONLY the title + description provided; do NOT fetch.

## WHO THIS CANDIDATE IS (judge fit against this)
<<WHO>>

## DECISION PROCEDURE (apply in order)
1. HARD GATES — if ANY applies, score 0-19 and stop:
<<HARD_GATES>>
2. ROLE TYPE: is it one of the target areas listed above? If none (e.g. IT support,
   QA-only, data entry, pure DBA), it is weak: 20-44.
3. STACK: count core overlaps with the strengths listed above (languages, frameworks,
   domains). 2+ core overlaps = strong; 1 = good/borderline; 0, or the core requirement
   is in the NOT-list = weak.
4. MODIFIERS: fully remote and junior/graduate wording push up; hybrid or vague
   seniority pull down slightly; startup/small team is a mild plus.

## SCORE ANCHORS (0-100; 60+ = "worth building a CV for")
Use the WHOLE scale decisively. A clear match deserves 90+, not a timid 70 — timid
mid-band scores on true matches are labeling ERRORS that poison the dataset. A clear
miss deserves < 20, not 40.
- 90-100  Exceptional: squarely in a target area, 2+ core stack overlaps, seniority
          within band, workplace policy passes. Apply today.
- 75-89   Strong: solid role + stack match with ONE soft gap (hybrid; one adjacent-but-
          new core tool; slightly heavier years wording).
- 60-74   Good: genuinely relevant role, 1 solid core overlap or a modest domain
          stretch; still clearly worth a tailored CV.
- 45-59   Borderline: real overlap but notable gaps (core requirement off-stack,
          ambiguous seniority, unclear remote policy).
- 20-44   Weak: technical but mostly mismatched stack/domain or wrong role type.
- 0-19    No fit: any hard gate.

## CALIBRATION EXAMPLES (score these the same way every time)
These are expressed relative to the candidate above, deliberately — the same six shapes
must mean the same thing for every candidate, or a template change could not be A/B
measured across users.
- A role in their FIRST target area, 2+ core overlaps, seniority in band, fully remote
  -> 93
- A role in a target area with a strong stack match but ONE soft gap (hybrid, or one
  adjacent tool they have not shipped) -> 80
- The right role type and workplace policy, but the CORE requirement is in their
  NOT-list; only general engineering overlap -> 55
- A "Senior"/"Lead" TITLE whose stated requirements are within their band, in an
  adjacent-to-target area they have real strength in -> 68
- Remote but locked to a region they cannot legally work in -> 12
- Two hard gates at once (e.g. on-site where they cannot go AND years far above band)
  -> 5

Fill score_breakdown FIRST, then set the final score consistent with it; a location FAIL
means the final score must be <= 19. Do not reward brand names or salary; score the WORK
against their strengths.

Return ONLY a JSON array, one element per job, no other text:
{
  "url": "the job url, copied exactly",
  "score": 0-100,
  "score_breakdown": {
    "technical": 0-100,        // stack / skill match against their listed strengths
    "experience": 0-100,       // seniority fit (from requirements, not title)
    "behavioral": 0-100,       // role type / team / culture signals
    "career_alignment": 0-100, // alignment with their target areas
    "location": "PASS/FAIL"    // workplace policy above = PASS; otherwise FAIL
  },
  "fit_notes": "1-2 sentence reason"
}

Process EVERY job. Keep the same url string you were given.

JOBS:
<JOBS_JSON>
