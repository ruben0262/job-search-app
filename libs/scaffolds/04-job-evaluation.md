# Job Evaluation Framework

## Scoring Dimensions

Evaluate each job posting against these five dimensions:

### 1. Technical Skills Match (0–100)
How well do the required/preferred skills align with the candidate's capabilities?

| Score | Meaning |
|-------|---------|
| 80–100 | Core requirements are primary skills |
| 60–79 | Most requirements match, 1–2 gaps that are learnable |
| 40–59 | Partial match, significant upskilling needed |
| 0–39 | Fundamental mismatch |

<!-- [FILLED BY /setup] — the three lists below are personalized from your profile
     (01-candidate-profile.md). Replace with your own strong / moderate / weak match areas. -->

**Strong match areas:**
- [your primary skills — the ones you'd score 80–100 on]

**Moderate match areas:**
- [skills you have but not at production depth]

**Weak match areas:**
- [stacks/domains that are a fundamental mismatch for you]

### 2. Experience Match (0–100)
Does work history align with what they're looking for?

| Score | Meaning |
|-------|---------|
| 80–100 | Direct experience in the same domain and role type |
| 60–79 | Related experience, transferable skills clear |
| 40–59 | Adjacent experience, would need to make the case |
| 0–39 | Unrelated experience |

<!-- [FILLED BY /setup] — personalized from your work history. -->

**Strong:**
- [domains/role types where you have direct experience]

**Moderate:**
- [adjacent experience you'd need to make the case for]

**Entry-level:**
- [areas where you have little or no track record]

### 3. Behavioral / Culture Fit (0–100)
Does the role and company culture match the behavioral profile?

| Score | Meaning |
|-------|---------|
| 80–100 | Culture strongly matches preferences |
| 60–79 | Mixed signals but mostly compatible |
| 40–59 | Some friction areas |
| 0–39 | Significant culture mismatch |

<!-- [FILLED BY /setup] — from your behavioral profile (02-behavioral-profile.md). -->

**Strong fit signals:** [culture attributes that match how you work best].

**Red flags:** [culture attributes that are friction for you].

### 4. Location & Remote (Pass/Fail + Notes)
<!-- [FILLED BY /setup] — from your Geographic Scope in CLAUDE.md. -->
- **Remote (any country):** PASS / FAIL — [your stance]
- **Home city hybrid:** PASS / FAIL
- **Other regions hybrid:** [PASS and flag / FAIL]
- **Mandatory relocation:** [your deal-breaker rule]

### 5. Career Alignment & Motivation (0–100)
Does this role advance goals and contain tasks that energize?

| Score | Meaning |
|-------|---------|
| 80–100 | Strongly aligned with career direction |
| 60–79 | Good role but only partially aligned |
| 40–59 | Decent job but doesn't build toward goals |
| 0–39 | Dead end or backwards step |

<!-- [FILLED BY /setup] — from your Target Roles + goals in CLAUDE.md. Keep anything private
     (e.g. a goal that must not appear in applications) noted here explicitly. -->

**Career goals:**
- [where you want your career to go — role types, company types]

**Motivation filter — tasks that energize:**
- [the kind of work you want more of]

**Tasks that drain:**
- [the kind of work to steer away from]

## Weighting
- Technical Skills: 30%
- Experience Match: 25%
- Behavioral Fit: 15%
- Career Alignment: 30%

(Location is pass/fail, not weighted)

## Thresholds
- **Strong Fit** (75+): Definitely apply, tailor CV
- **Good Fit** (60–74): Apply, address gaps
- **Moderate Fit** (45–59): Consider carefully; discuss with user
- **Weak Fit** (30–44): Probably skip unless strategic
- **Poor Fit** (<30): Skip

## Output Format

```
## Job Fit Evaluation: [Role] at [Company]

| Dimension | Score | Notes |
|-----------|-------|-------|
| Technical Skills | XX/100 | [brief note] |
| Experience Match | XX/100 | [brief note] |
| Behavioral Fit | XX/100 | [brief note] |
| Location / Remote | PASS/FAIL | [brief note] |
| Career Alignment | XX/100 | [brief note] |

**Overall Score: XX/100**

### Verdict: [Strong Fit / Good Fit / Moderate Fit / Weak Fit / Poor Fit]

### Angle to Take
There is one base template (`cv/base_cv.html`). State the ANGLE this posting calls for: which side of the
candidate's background leads, how the other entries get reframed, and what gets cut. See `05-cv-templates.md`
("CV Content Judgement — Tailoring by Role-Type Lens") and CLAUDE.md's per-angle rules.
- **Leads:** [which experience goes first, and why]
- **Reframed:** [what gets told through this role's lens]
- **Cut:** [what does not earn its place here]

### Key Strengths for This Role
- [bullet points]

### Gaps to Address
- [bullet points]

### Application Details Captured
- Application URL: [URL or email]
- Method: [direct / linkedin / email / portal]
- Required docs: [CV only / CV + cover letter / other]
- Cover letter required: [yes / no]
- Contact person: [name + email or N/A]
- Deadline: [date or N/A]
- Language: [posting language]
```
