You are evaluating job postings for the candidate. Your CLAUDE.md and skills
are loaded — use .claude/skills/job-application-assistant/04-job-evaluation.md
for scoring criteria.

## PART A: Score provided links

For each job URL in the JOBS TO SCORE list below, fetch the page with WebFetch,
read the job description, and score it against the candidate profile.

## PART B: Search supplementary sites for additional links

After scoring all provided links, search these additional sites for jobs posted
in the LAST 24 HOURS ONLY. Use WebSearch with the exact queries below. For each
result that looks relevant and is from the last 24 hours, fetch it and score it
the same way as Part A.

SUPPLEMENTARY SEARCHES:
<CLAUDE_SUPPLEMENTARY_SEARCHES_HERE>

For each search, use WebSearch with: "<search_prefix> <keyword>"
Example: WebSearch "site:openquant.co quantitative researcher"
Only include results posted in the last 24 hours. Skip any result whose URL
already appears in the ALREADY_SEEN list below.

ALREADY_SEEN (skip these URLs):
<ALL_LINKS_URLS_HERE>

## Output format

Return ONLY a JSON array. No other text. Each element:
{
  "url": "the original URL",
  "title": "job title",
  "company": "company name",
  "location": "location",
  "is_remote": true/false,
  "date_posted": "YYYY-MM-DD or null if unknown",
  "score": 0-100,
  "score_breakdown": {
    "technical": 0-100,
    "experience": 0-100,
    "behavioral": 0-100,
    "career_alignment": 0-100,
    "location": "PASS/FAIL"
  },
  "cover_letter_required": true/false,
  "application_url": "direct application URL if visible",
  "deadline": "YYYY-MM-DD or null",
  "language": "english/hungarian",
  "source": "provided/openquant/efinancialcareers/web3career/etc",
  "notes": "2-3 sentence fit summary",
  "error": null or "reason if URL couldn't be fetched"
}

Include BOTH Part A results (from provided links) and Part B results (from
supplementary searches) in the same output array. Mark Part B results with their
source name. If a URL fails to load, set score to 0 and error to the reason.
Process ALL URLs — do not stop early.

JOBS TO SCORE:
<LINKS_JSON_HERE>
