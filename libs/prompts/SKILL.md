# Job Application Assistant

**name:** job-application-assistant
**description:** Assists with job applications: evaluating job postings, tailoring CVs, writing cover letters, and preparing for interviews. Triggers on keywords like: job posting, job application, CV, cover letter, resume, interview prep, job fit, career, application, apply
**allowed-tools:** Read, Glob, Grep, WebFetch, WebSearch, Edit, Write, AskUserQuestion

---

## Workflow

When the user provides a job posting (URL or text), follow this workflow:

### Step 1: Research & Evaluate Fit
- Fetch the job posting content (use WebFetch for URLs)
- Analyze the posting for required competencies, keywords, and priorities
- Research the company (website, LinkedIn, mission, recent news)
- Score the posting against the candidate's profile using the framework in `04-job-evaluation.md`
- Present the evaluation table and verdict
- Suggest whether the candidate should call the employer before applying (see `04-job-evaluation.md` for guidance)
- Ask the user if they want to proceed with an application

### Step 2: Tailor CV
- Copy the base template `cv/base_cv.html` (see `05-cv-templates.md`) and decide the angle from the posting
- Create `cv/jobs/<YYYY-MM-DD>/<company-slug>/source.html` with tailored content, then compile to `<Name>_CV.pdf`
- Adjust: profile statement, competency bullets, experience bullet emphasis, section order

### Step 3: Write Cover Letter
- Only if the posting explicitly requires one, or the candidate explicitly asks for one regardless
- Follow the writing style rules in `03-writing-style.md` (critical: no em-dashes, no cliches, no AI-tell repetition, honest gap disclosure)
- Follow the template structure in `06-cover-letter-templates.md`
- Create `cv/jobs/<YYYY-MM-DD>/<company-slug>/cover.html` in the same folder as the CV, then compile to `<Name>_Cover.pdf`
- Ensure the letter connects specific experience to the role requirements

### Step 4: Interview Preparation
- Follow the framework in `07-interview-prep.md`
- Prepare STAR-format answers for likely questions
- Identify role-specific talking points
- Draft questions the candidate should ask the interviewer

---

## Reference Files

| File | Purpose |
|------|---------|
| `01-candidate-profile.md` | Education, experience, skills, publications, awards |
| `02-behavioral-profile.md` | Behavioral assessment, strengths, ideal environments |
| `03-writing-style.md` | Tone, structure, do's and don'ts |
| `04-job-evaluation.md` | Scoring framework for job fit |
| `05-cv-templates.md` | HTML CV structure and tailoring rules |
| `06-cover-letter-templates.md` | HTML cover letter structure and tailoring rules |
| `07-interview-prep.md` | STAR examples, tough questions, roleplay guidelines |

---

## Quick Commands

The user may also ask for individual steps without the full workflow:
- "Evaluate this job posting" - Step 1 only
- "Write a CV for [company]" - Step 2 only
- "Write a cover letter for [role] at [company]" - Step 3 only
- "Help me prepare for an interview at [company]" - Step 4 only
- "What jobs should I look for?" - Career strategy discussion using profile + evaluation framework
