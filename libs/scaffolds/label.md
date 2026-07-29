You are scoring job postings for the candidate described below to build a TRAINING dataset, so
CONSISTENCY is the priority: a job of a given quality must get the same score no matter what other
jobs are in the batch. Score each job INDEPENDENTLY against this rubric, never relative to the rest
of the batch. Score from ONLY the title + description provided; do NOT fetch.

<!-- ─────────────────────────────────────────────────────────────────────────────
[FILLED BY /setup-scorer] — this file is the SINGLE definition of "fit" your local model
learns. It is deliberately SELF-CONTAINED (the labeller runs without CLAUDE.md loaded, to keep
labelling cheap), so every fact it needs must be written here.

Replace every [BRACKETED] block below with your own details. Keep the STRUCTURE exactly as it
is — the ordered decision procedure, the full-range anchors, and especially the calibration
examples are what make labels consistent, and consistency is what makes the model good.
The method (and how to maintain it) is documented in docs/SCORING_RUBRIC_GUIDE.md.

Editing this file changes `profile_version`, which invalidates existing labels. After any edit:
`python -m scorer.label --relabel` (cheap, re-scores stored JDs) then retrain.
───────────────────────────────────────────────────────────────────────────── -->

## WHO THE CANDIDATE IS (judge fit against this)
- [Seniority line: level + years-of-experience tolerance. Be explicit about how to read a
  mismatch between a job's TITLE and its stated REQUIREMENTS — e.g. "junior-to-mid; fine up to
  ~4 years; NOT a fit where 5+ years or senior scope is a hard requirement in the REQUIREMENTS
  (a 'Senior' title with mid-level requirements is fine)".]
- Strengths, each with real supporting work:
  - [Strength area 1 — the concrete skills/tools/domains, as they appear in job postings]
  - [Strength area 2]
  - [Strength area 3]
  - [Strength area 4 — 3-5 areas total; be specific, these drive the STACK step below]
- Does NOT have professional experience in: [the explicit NOT-list — stacks/domains adjacent
  enough that a naive reader would give partial credit, but which are genuinely not yours.
  Without this list, off-stack roles quietly collect points. E.g. "enterprise Java/.NET at
  depth, mobile, embedded, hardware, pure design/UX".] Roles whose CORE requirement is one of
  these are off-stack (some overlap via general skill, but not a primary match).
- Targets, priority order: (a) [primary target role type + company type]; (b) [secondary];
  (c) [tertiary].
- Employment types: [which are welcome and which are not — e.g. "contract, freelance and
  part-time remote are as welcome as full-time; unpaid or equity-only is a dealbreaker".]
- Workplace policy: [remote/hybrid/on-site stance, and where on-site is acceptable. Name the
  places you would genuinely commute to; everything else on-site is a dealbreaker.]
- Languages: [languages you can work in]. A JD written in any other language means the employer
  expects that language — hard gate.

## DECISION PROCEDURE (apply in order)
1. HARD GATES — if ANY applies, score 0-19 and stop:
   - [role is outside your field entirely — define it, e.g. "no engineering or maths component"]
   - [seniority gate — e.g. "5+ years or senior-level scope required in the requirements"]
   - [location gate — e.g. "on-site-only outside <your on-site region>, or mandatory relocation"]
   - remote but restricted to a region you cannot legally work (e.g. "US only / must be
     authorized to work in the US" with no international option)
   - JD language is not one of your languages
   - [compensation gate — e.g. "unpaid / equity-only"]
2. ROLE TYPE: is it one of your targets above? If none (e.g. [name the adjacent-but-wrong role
   types you keep seeing — support, QA-only, data entry]), it is weak: 20-44.
3. STACK: count core overlaps with your strengths (skills, tools, domains listed above).
   2+ core overlaps = strong; 1 = good/borderline; 0 (core is in your NOT-list) = weak.
4. MODIFIERS: [what pushes up or pulls down — e.g. "fully remote and junior/graduate wording
   push up; hybrid or vague seniority pull down slightly; startup/small team is a mild plus".]

## SCORE ANCHORS (0-100; 60+ = "worth building a CV for")
Use the WHOLE scale decisively. A clear match deserves 90+, not a timid 70 — timid mid-band
scores on true matches are labeling ERRORS that poison the dataset. A clear miss deserves < 20,
not 40.
- 90-100  Exceptional: right seniority, squarely in a target area, 2+ core stack overlaps,
          workplace policy fully satisfied. Apply today.
- 75-89   Strong: solid role + stack match with ONE soft gap (e.g. hybrid; one adjacent-but-new
          core tool; slightly heavier years wording).
- 60-74   Good: genuinely relevant role, 1 solid core overlap or a modest domain stretch; still
          clearly worth a tailored CV.
- 45-59   Borderline: real overlap but notable gaps (core requirement off-stack, ambiguous
          seniority, unclear remote policy).
- 20-44   Weak: in-field but mostly mismatched stack/domain, or wrong role type.
- 0-19    No fit: any hard gate.

## CALIBRATION EXAMPLES (score these the same way every time)
[FILLED BY /setup-scorer — write ~6 examples from YOUR profile, in this exact shape: a realistic
one-line posting -> the score -> the reason in brackets. This is the single biggest consistency
lever in the file. Cover the whole range: one 90+, one 75-89, one 60-74, one 45-59 borderline,
and at least TWO hard-gate examples (one location/region-lock, one seniority) so the gates are
unmistakable. The examples below show the FORMAT and reasoning style — replace them all.]
- "[Junior <target role> (<core tool>) — remote <your region>, <company type>; <core tool>,
  <core tool>, 1-3 yrs]" -> 93 (target role, 2+ core overlaps, remote, junior)
- "[<Target role> — hybrid <city>; <core tool>, <adjacent tool>, 2-4 yrs]" -> 80 (strong stack
  and target; hybrid is the one soft gap)
- "[<Target role type> (<NOT-list tool>) — remote, 3 yrs]" -> 55 (right role type and remote,
  but the core stack is in the NOT-list; only general overlap)
- "[Senior <target role> — fully remote worldwide; requirements: 2+ yrs <core tool>]" -> 68
  (senior in TITLE only, requirements are mid; real strength area)
- "[<Target role> — remote, US only, must be authorized to work in the US]" -> 12 (hard gate:
  region-locked remote)
- "[<Target role> — on-site <city outside your on-site region>; <advanced degree> + 5 yrs]" -> 5
  (two hard gates: on-site outside the accepted region, and 5+ yrs)

Fill score_breakdown FIRST, then set the final score consistent with it; a location FAIL means
the final score must be <= 19. Do not reward brand names or salary; score the WORK against the
strengths above.

Return ONLY a JSON array, one element per job, no other text:
{
  "url": "the job url, copied exactly",
  "score": 0-100,
  "score_breakdown": {
    "technical": 0-100,        // stack / skill match against the listed strengths
    "experience": 0-100,       // seniority fit (from the requirements, not the title)
    "behavioral": 0-100,       // role type / team / culture signals
    "career_alignment": 0-100, // alignment with the target roles above
    "location": "PASS/FAIL"    // workplace policy satisfied = PASS; otherwise FAIL
  },
  "fit_notes": "1-2 sentence reason"
}

Process EVERY job. Keep the same url string you were given.

JOBS:
<JOBS_JSON>
