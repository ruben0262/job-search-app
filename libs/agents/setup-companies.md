# /setup-companies - ATS Company List Seeding

You are building the user's direct-ATS company universe — the set of company career boards the
pipeline polls **directly**, bypassing job boards entirely. These are the highest-quality jobs
in the system (real employer postings, real timestamps, direct apply links, no aggregator lag),
so this stage is worth doing properly.

Nine ATS platforms are supported (`pipeline/sources/ats.py`): **Greenhouse, Ashby, Lever,
SmartRecruiters, Recruitee, Breezy, Workday, Personio, Workable**. Two VC portfolio boards
(`consider` = a16z/Sequoia, `getro` = Accel) feed the same harvester.

The list is **self-growing**: every apply URL the pipeline ever sees is scanned for a known ATS
board and recorded into `pipeline/data/ats_tokens.json`. Polling stays cheap regardless of size
because ETag/304 conditional requests make unchanged boards nearly free. But it needs relevant
starting mass — that is this command's job.

**Prerequisites:**
- `/setup-boards` must be done — check for the `# personalized:` marker on `searches:` in
  `pipeline/config.yaml`. **Both** paths below depend on it: `--deep` re-runs the configured
  hiring.cafe queries, and the curated import validates candidates against the config's per-ATS
  `keywords`. Run against the wrong field's config and you seed the wrong companies.
- **Environment:** use Docker if the stack exists (`docker compose run --rm pipeline python -m …`),
  otherwise the local venv (`.venv/Scripts/python -m …` on Windows). Detect once, use consistently.

---

## Step 1: Explain the model and check the starting point

Read `pipeline/data/ats_tokens.json` if present and report the token count per ATS. Explain in
two sentences: the config's `companies:` lists are only SEEDS; the harvested file is the real
list (unioned in via `use_harvested: true`) and it grows by itself from every scrape. Today we
(a) seed companies in THEIR field, and (b) run a bulk harvest.

## Step 2: Ask before proposing (these change the answer)

1. **Dream companies.** "Which companies would you actually want to work for?" — they know
   their industry's employers better than you do. Every named company that uses a supported ATS
   is a direct line to that employer's real postings.
2. **Company size/type.** Startups (Ashby, Lever, Greenhouse, Workable) vs large enterprises
   (Workday, SmartRecruiters) vs EU/DACH SMBs (Personio) — the ATS distribution differs sharply
   by segment, and their answer tells you which platforms are worth seeding at all.
3. **Regions.** A company list skewed to the wrong continent wastes polling budget; the ATS
   location pre-filter (`&ats_locs`) already screens postings, but seeding the right companies
   is what makes the poll productive.

## Step 3: Replace the example seed lists

The shipped `companies:` seeds are software-startup examples (stripe, openai, …). Propose
10–25 companies from Step 2 across the ATSes their segment actually uses. Prioritize platforms
where you can name real companies confidently; leave the rest `[]` — the harvest fills them in.
Confirm the list with the user.

**Token formats** (the slug in the board URL):
| ATS | Token | From a URL like |
|---|---|---|
| greenhouse | `<token>` | `boards.greenhouse.io/<token>` or `job-boards.greenhouse.io/<token>` |
| lever | `<token>` | `jobs.lever.co/<token>` (also `jobs.eu.lever.co/`) |
| ashby | `<token>` | `jobs.ashbyhq.com/<token>` |
| smartrecruiters | `<token>` | `jobs.smartrecruiters.com/<token>` |
| recruitee | `<token>` | `<token>.recruitee.com` |
| breezy | `<token>` | `<token>.breezy.hr` |
| personio | `<token>` | `<token>.jobs.personio.de` or `.com` |
| workable | `<token>` | `apply.workable.com/<token>` or `<token>.workable.com` |
| **workday** | **`tenant:wdN:site`** (composite, all three parts) | `<tenant>.<wdN>.myworkdayjobs.com/<site>/job/…` — e.g. `nvidia:wd5:nvidiaexternalcareersite` |

If unsure of a slug, guess the obvious lowercase name: dead tokens are skipped harmlessly (404s
are cached as dead) and `--prune` cleans them later. Keep tokens lowercase — they are
case-normalised anyway.

## Step 4: Bulk harvest

Show each command before running it.

1. **Deep harvest — the main event, and field-agnostic.** Re-runs the user's OWN configured
   hiring.cafe queries with a wide window (30 days) and deep pagination, purely for the harvest
   side effect. Every hit's board token and apply-URL is recorded. These tokens are
   pre-relevant (each came from a real job matching their queries), so they skip validation.
   Typically hundreds of companies in one pass.
   ```
   python -m pipeline.seed_tokens --deep
   ```
2. **Curated import — TECH-ONLY, skip it otherwise.** Downloads community-maintained company
   lists from GitHub (`hiring-without-whiteboards`, `easy-application`) and validates every
   candidate (board alive AND at least one current posting matching the config keywords for
   that ATS). **Those lists are software-company READMEs.** If the user is not in tech, say so
   plainly and skip this step — validation would reject nearly everything and the yield is not
   worth the wait. Preview first if you like:
   ```
   python -m pipeline.seed_tokens --dry-run     # report only, writes nothing
   python -m pipeline.seed_tokens               # download, validate, merge
   ```
3. **Prune** — re-validates stored tokens and drops dead/gated boards. Safe: any board that
   hires again is re-harvested automatically from any stream.
   ```
   python -m pipeline.seed_tokens --prune
   ```

## Step 5: Report and mark complete

Re-read `pipeline/data/ats_tokens.json`; show before → after per ATS and the total.

- A healthy starting universe is **150+ companies**; after one `--deep` pass several hundred is
  normal for a well-configured tech profile. Fewer is expected and fine in a niche field or a
  small market — the list grows on its own from every scrape.
- **Under ~50 and the user is in tech?** Their hiring.cafe queries are probably too narrow —
  revisit `/setup-boards` Step 1, then re-run `--deep`.
- **Under ~50 in a non-tech field?** Often just reality: fewer employers in that field use these
  ATSes. Say so honestly rather than implying something is broken. The Tier-1/Tier-2 boards
  carry those markets instead.
- Ongoing: `--deep` then `--prune` about monthly (`docs/COMMANDS.md` §7b).

Stage complete when the total is ≥150, **or** the user explicitly accepts a smaller universe.
State which of the two applied — `/setup-all` asks.
