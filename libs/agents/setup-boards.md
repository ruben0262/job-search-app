# /setup-boards - Job Boards & Filter Configuration

You are personalizing `pipeline/config.yaml` (and the filter variables in `.env`) for this
user's field, geography and preferences. Everything shipped in `searches:` is an EXAMPLE from a
remote-leaning software-engineering profile in Europe — every query, keyword list and region in
it must end up reflecting THIS user, or the pipeline scrapes and scores the wrong jobs all day.

This is the difference between a feed full of relevant jobs and a feed full of noise, so do it
properly: it is worth 15 focused minutes.

**Prerequisite:** the profile must be populated (run `/setup` first). Read `CLAUDE.md`'s
Candidate Profile — if it still contains `[FILLED BY /setup]` / `[YOUR NAME]` placeholders, stop
and tell the user to run `/setup` first. Otherwise use the profile (Target Roles, Geographic
Scope, Languages, Deal-breakers, salary expectations) to PRE-FILL every question below — show
your inferred answer and ask them to confirm or correct. Never make them start from a blank page.

**Read before editing:** `pipeline/config.yaml` top to bottom (it is heavily commented and the
comments explain each source's quirks), and `docs/PROJECT_STATE.md` §Sources + §Filters.

**Hard rule — never touch the time/recency knobs.** They are a fixed design constant, identical
for every user, because the watcher's entire job is catching jobs while they are fresh:
`hours_old`, `posted_within_hours`, `posted_within_days`, `default_max_age_hours`,
`watcher.max_age_hours`, `watcher.interval_min`. If the user asks, explain why and move on.

**Two structural things to respect:**
- **YAML anchors.** `&ats_keywords`, `&ats_excludes`, `&ats_locs` are defined once in the
  `greenhouse` block and aliased (`*ats_keywords`, …) by ~10 ATS and VC-board sources;
  `&remote_kw` is defined in `jobicy` and aliased by `themuse`. **Edit the anchor definition
  once — never expand an alias into a copy.** That is what keeps 12 sources consistent.
- **`enabled: true|false` on every source.** Disabling a source that does not fit the user's
  market is a NORMAL, encouraged outcome — not a failure. A disabled source costs nothing.

---

## Step 1: Interview (pre-filled from the profile, confirmed by the user)

One compact message per topic. Show your inference, ask to confirm/edit.

1. **Role keywords.** The 8–15 search terms describing their target roles. Include modern
   variants for the same work (titles get renamed — search the variations) and, where the
   profile suggests it, junior/graduate variants. These drive nearly every source.
2. **Regions + workplace policy.** For each region they will work in, ask: remote-only,
   hybrid-ok, or would they actually go to an office there? This decides:
   - regions where they would attend an office → `ONSITE_LOCATIONS` in `.env` (comma-separated
     lowercase substrings, e.g. `germany,berlin`). Empty = on-site anywhere is accepted.
   - remote-only regions → queries with `is_remote: true`; on-site-ok regions → queries WITHOUT
     `is_remote` (so hybrid/on-site come through there too).
   - the `&ats_locs` anchor → their regions/cities + `remote` (+ synonyms like `emea`).
3. **Seniority.** Ask which direction applies:
   - Early-career → hard-block senior titles. Default = `senior`, `sr`, `sr.` only, so
     lead/staff still reach the scorer (recommended — titles lie, requirements do not).
     Offer stricter: add `staff`, `principal`, `lead`, `head of`, `director`, `vp`, `chief`.
   - Senior → block the junior end instead: `junior`, `graduate`, `intern`, `trainee`,
     `entry level`, `working student`.
   Applies to BOTH layers: `phase1_filters.title_exclude_keywords` AND the `&ats_excludes`
   anchor (keep its non-seniority noise entries — `sales`, `marketing`, `recruit`, `designer`,
   … — unless the user's field IS one of those, in which case remove that entry).
4. **Field gate.** `phase1_filters.known_tech_keywords` is the "is this even my field?" gate.
   Rebuild it in THEIR vocabulary (a nurse's list looks nothing like a developer's). If their
   field's postings are too varied for a keyword gate, set `require_known_tech: false` and say
   you did it and why. NOTE: it only applies to real descriptions (≥200 chars), by design.
5. **Languages.** Which languages can they realistically apply in? → `JOB_LANGUAGES` in `.env`
   (ISO codes, e.g. `en,de`; empty disables). If a target region is non-English-speaking and
   they apply in that language, plan local-language job titles for that region's queries.
6. **Salary bands** (drives the feed's 🎯 line). Ask for their target range per region they
   target — the number they would actually accept, quoted the way local boards do (monthly in
   HU/PL/CZ, annual elsewhere). Tell them the point: every card shows what the job pays next to
   what they should ask, with a ⚠️ when the posting is under their floor. If they would rather
   not decide now, leave `salary_bands` commented out — the feed just omits the 🎯 line.
7. **Country-specific boards.** The shipped config includes Hungarian-market boards
   (`nofluffjobs_hu`, `profession`, `dreamjobs`, `jooble`) as an EXAMPLE of covering a local
   market. Ask: do they work a specific national market? If not → disable all four. If yes but
   a different country → disable these and point them at `docs/NEW_SOURCE_CHECKLIST.md` to add
   their own (do NOT attempt to write a new source adapter inside this command).
8. **Optional free API sources.** `findwork`, `adzuna` and `jooble` need free API keys and
   self-disable without them. Ask whether they want them; if yes, give the registration links
   from `.env.example` and have them paste the keys into `.env`.

## Step 2: Write `pipeline/config.yaml`

Work through the file in order. Preserve structure, comments and every knob not listed here.

**Filters**
- `phase1_filters.title_exclude_keywords` — from Q3.
- `phase1_filters.require_known_tech` + `known_tech_keywords` — from Q4.
- `phase1_filters.blocked_url_keywords` — leave as-is (gatekeeper "job platforms" that hide the
  application behind a signup wall; field-agnostic). Mention it exists so they can add offenders.

**Salary** (from Q6)
- Uncomment and fill `salary_bands:` — region keys `UK / EU / CH / HU / PL / GLOBAL`, each
  `{ currency, period, band: [lo, hi] }`. Only include regions they actually target.

**Tier 1 (JobSpy)**
- `jobspy_linkedin.queries` — ~15–30 entries = core role terms × their regions, `is_remote` per
  Q2. Keep 3–4 dedicated `easy_apply: true` passes on the top terms in the primary region.
- `jobspy_indeed` — `countries` (Indeed is country-scoped, no region-wide queries) × `keywords`
  is a cross-product; set both to theirs. The extra `queries:` list is for explicit
  local-language/city passes — replace or empty it.
- `jobspy_google.queries` — free-text `google_search_term` strings; rewrite in their words.

**hiring.cafe**
- `queries` mirror the LinkedIn intent. Supported `location` values are ONLY those captured in
  `pipeline/sources/hiringcafe.py` (currently `europe | united kingdom | budapest`). For another
  region: use the closest, and tell the user the file documents how to capture their own
  location object from hiring.cafe's parse-filters API. Do not invent a location string — an
  unknown key is skipped with a warning.

**Direct ATS + VC boards** (greenhouse, ashby, lever, smartrecruiters, recruitee, breezy,
workday, personio, workable, consider, getro)
- Update the three anchors ONCE in the `greenhouse` block: `&ats_keywords` (their role terms),
  `&ats_excludes` (Q3 + noise), `&ats_locs` (their regions + `remote`). All 10+ aliases follow.
- `companies:` seed lists → leave them; `/setup-companies` owns those. Say so.
- `consider`/`getro` also have `queries:` and `boards:` — update `queries` to their terms; leave
  `boards` (the VC job-board instances) alone.

**Tier-2 boards** — each has its own shape; set the keyword/tag list to theirs, and DISABLE any
whose market or field does not fit (say which you disabled and why):
- `nofluffjobs` (Poland), `justjoinit` (Poland) — disable unless they target Poland.
- `arbeitnow` (DE-leaning), `remotive`, `himalayas`, `remoteok` (`tags:`), `weworkremotely` +
  `workingnomads` (`keywords:` + `feeds:` — the feeds are category URLs; swap for their field's
  feeds or disable), `jobicy` (`tags`, `geo_keep`, and the `&remote_kw` anchor),
  `themuse` (`categories` are from The Muse's fixed taxonomy — pick theirs; `locations`),
  `adzuna` (`countries`, `where`, `keywords`), `findwork` (`searches`),
  `hn_hiring` (`keywords` — worth keeping for almost any tech-adjacent field),
  `claude_supplementary` (`sites:` — niche boards searched via Claude; set to their field's).
- Country-specific block (Q7): disable or keep as decided.

**Do not touch:** `model`, `cv_model`, `cv_thinking_tokens`, `cv_engine`, `scoring.*`,
`watcher.*`, `limits.*`, `proxy`, `telegram`. Those are `.env`-driven or tuned defaults; other
stages and docs own them.

## Step 3: Write `.env`

If `.env` is missing, tell the user to `cp .env.example .env` first (point at
`docs/GETTING_STARTED.md` Step 4). Then set, in place:
- `JOB_LANGUAGES` (Q5), `ONSITE_LOCATIONS` (Q2).
- `MAX_LINKEDIN_APPLICANTS` — mention it: LinkedIn jobs at/above the 200-applicant display cap
  are dropped as stale/reposts. 0 disables. Default 200 is sensible; leave unless they object.
- Any API keys from Q8.
- `WATCHER_HU_CHAT_ID` — only if they kept a country-specific market AND want it in its own
  Telegram channel. Otherwise leave blank (everything goes to the main feed).

Never echo secret values back into the chat.

## Step 4: Validate and mark complete

1. **Parse-check** (an anchor typo silently breaks many sources):
   ```
   python -c "import yaml,io; d=yaml.safe_load(io.open('pipeline/config.yaml',encoding='utf-8')); print(len(d['searches']),'sources OK')"
   ```
   (`.venv/Scripts/python` on Windows if plain `python` lacks PyYAML.) Fix any error before
   continuing.
2. **Sanity-check the anchors resolved**: print one aliased source's `keywords` and confirm it
   matches the anchor you wrote — this catches an accidentally-expanded alias.
3. Show a compact summary: role terms | regions + policy | seniority | field gate | languages |
   salary bands | sources enabled vs disabled. Get an explicit "yes".
4. On confirmation, set the completion marker: the `searches:` line must carry a comment
   `# personalized: <field> <YYYY-MM-DD>`. `/setup-all` gates on this.
5. If the stack is already running, offer a live smoke test and read it together:
   ```
   docker compose run --rm watcher python -m watcher.watcher --once
   ```
   Expect `found / new / high / notified` counts. `found: 0` at a quiet hour is normal;
   `found: 0` on every source suggests the queries are too narrow — revisit Step 1.
   Pre-model, "scorer service not ready" is expected (see `/setup-scorer`).
