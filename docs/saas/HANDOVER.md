# Handover — 2026-07-30

Where the platform actually is, what is left, and the decisions a new session would otherwise have
to re-derive. `CHANGELOG.md` is the history; this is the position.

---

## Live right now

| | |
|---|---|
| Site + API | `https://mahoraga-app.rubenlekli.com` (Cloudflare Tunnel; port 22 is the only public listener) |
| Box | `ruben@169.58.16.241`, checkout at `~/job-search-app` |
| Running | postgres, api, capture, labeller, scorer, notifier, trainer, docgen, cloudflared, embedder |
| Timers | **all five installed and enabled** — harvester 15 min, notify-dispatch 60 s, linkedin-describe hourly, health, backup |
| Tests | 966 passing, 2 skipped, against a throwaway Postgres |
| Accounts | `lekliruben@gmail.com` (full profile, model servable), `andraslekli@gmail.com` (config only) |

`sudo deploy/install-units.sh` installs and enables every timer idempotently, and `check_health.py`
**pages** when one is missing — a timer that was never installed is invisible from inside the
application, which is how `notify-dispatch` was absent for weeks while the stack looked healthy.

---

## The thing most likely to mislead a new session

**The JobSpy tier is not enabled.** `source_config.DEFAULT_ENABLED` is seven no-auth REST sources.
LinkedIn, Indeed and Google are not harvested at all, and every LinkedIn row in the corpus — 3,495
of them — arrived in a single import on 2026-07-27.

So "LinkedIn jobs are old" is not a filter bug. There is no LinkedIn ingestion. The per-user
LinkedIn tier is designed (`services/harvester/governor.py` holds the concurrency ceiling, the
interval floor and the kill switch) and **was never built**. The proxy, the lease accounting and the
per-user description pass all exist and work; what is missing is the loop that searches LinkedIn on
a user's own schedule with their own credential.

The delivery-side filters do now catch the stale imported rows: a dry run dropped 33 as too old and
17 as closed, where all 50 would previously have been sent.

---

## Order of work agreed with the owner (2026-07-30)

**The site is finished before any prompt is touched.** Prompts are measurable now and can wait; a
half-working product cannot be evaluated at all.

### 1. Jobs page — the priority

Currently not a table. It needs:

- a real table with the columns that matter (score, title, company, location, source, posted age,
  salary, applied state)
- **filters** on those columns
- a **search box** over job titles and links
- **pagination**

`GET /feed` already returns the rows, so this is largely a web change. A `jobs_detail` page — click
a row, see everything about that posting — was explicitly deferred, but should exist eventually.

### 2. Profile / setup chat

- The chat should feel like talking to Claude: **a typing indicator while the model is working.** A
  turn can take minutes and the current pending line is static.
- **"What we have written down" cannot be opened.** The file list renders but clicking does nothing.
  It should open the file, and the entries want **names and icons** rather than filenames, so a
  non-technical user can tell what each one is.

### 3. Dashboard, onboarding, settings

Reviewed by the owner on 2026-07-30 and judged good. Nothing outstanding.

### 4. Prompts — after the above

The next experiment is already designed: cut the Core Competencies block and require an outcome on
every retained bullet, **without** capping total length, plus floors — never fewer than three
experience entries, never drop Education. The previous variant failed precisely because it capped
length at the same time.

---

## The measurement stack, and why it can be trusted

| Script | Question it answers |
|---|---|
| `deploy/scripts/calibrate_judge.py` | Does the judge discriminate at all? |
| `deploy/scripts/ab_prompts.py` | Is prompt A better than B, and where is it losing? |
| `libs/scoring/cveval.py` | The objective checks the judge is told already exist |

The judge passed all three calibration probes by 23–51 points — tailored over untailored, the right
job over a distant one, real over a mechanically degraded copy — ordering correctly on **every** job
rather than on average.

Absolute scores run 50–60 for a tailored CV. The owner independently rated one of his own real CVs
at "40, maybe 50", so the scale is roughly honest rather than punitive.

**Known limits.** Six jobs per variant is a small sample and run-to-run variance is large: the same
prompt produced a 1-page/3,522-character CV on one run and 2-page/5,393 on the next. Gaps under
about 5 points are noise. The judge needs an Opus-class model — Haiku is the writer, and a judge no
better than the author cannot see the author's mistakes.

---

## Known wrong or unfinished

- **The JobSpy tier does not exist** (above). The largest gap.
- **The free-tier description pass fills few descriptions.** A recent cycle: 66 attempted, 0
  described, mostly `generic_http_200` — the page fetched fine and its text did not beat the snippet
  already stored. The JSON-LD path works well when it matches; the fallback rarely wins.
- **The shared harvest window is the widest any active account asked for.** Andras asks for 72
  hours, so the founder's 6 does not tighten the *fetch*. It does tighten his delivery, which is
  where it matters, but a second account raises everyone's fetch cost.
- **`users.profile_context` is a dead column**, superseded by `user_profile_files['context.md']`.
- **The context-file upload step is not built.** Agreed design: after onboarding, explain what a
  context file is, let the user write and upload one, then chat to Opus with it already in hand.
- **`jobs_detail` page** does not exist.
- **Phase 5 of `REBUILD_PLAN.md`** — deleting the schema machinery (`rubric.py`, `facts.py`,
  `docprompt.py`, `templates/cv.py`, the confirmation form, the generated types) — is not started.
- **Andras has no profile.** He was always going to run the setup chat as the real test of it.
- **Cover letters have no trigger.** `tailor_cover.md` is ready and nothing says *when* a posting
  wants one; the reference read a `cover_letter_required` flag this corpus does not have.

---

## Decisions that should not be re-litigated

- **Three file classes.** Platform-owned prompts (`libs/prompts/`), per-user files
  (`user_profile_files`), scaffolds (`libs/scaffolds/`). A scaffold never grounds a generation call.
- **Role angles are per user**, in `08-role-angles.md`, because the reference's two lenses were
  right for one person and wrong for everyone else. Measured to cost nothing (+1.7, noise).
- **Prompts are SACRED**: no edit without an A/B and a changelog entry. The A/B exists now, so this
  is enforceable rather than aspirational.
- **Every filter fails open except facts about the posting.** Closed and too-old are facts.
  Language, workplace and applicant count are preferences, and keep the job when unknown.
- **The user's own credential pays for everything**, and secrets are decrypted only inside
  `SecretStore.use` under `redacting()`.
- **Transport retries are two; the logic-level retry is one.** Eight was masking a bug.
- **The card's green dot is the delivery cutoff.** One number, passed from the send decision, so a
  job cannot be sent and called borderline in the same breath.
- **`max_notifications_per_day` is out of the onboarding form**, kept in the schema with its
  default. It is a plan limit, not a search preference, and becomes a tier setting when Stripe
  arrives — the seam is the `plans` table, where adding a tier is an INSERT.

---

## Operational notes worth keeping

- **Claude OAuth requires the Claude Code identity block as the first system block.** Without it,
  every Sonnet and Opus call returns `rate_limit_error` no matter how long you wait.
- **Structured output** rejects `minimum`, `maximum`, `maxItems` and `maxLength`, and requires
  `additionalProperties: false` on *every* nested object.
- **`thinking` is charged inside `max_tokens`.** A 32,000 ceiling with a 4,096 budget leaves about
  28,000 for the document.
- The host venv needs `anthropic` installed for the A/B and calibration scripts; the service images
  already have it.
- Run long scripts on the box with `setsid nohup … &` and poll the output file. `pgrep -f name`
  matches the ssh command running it — use `pgrep -f "[n]ame"`.
- **Never chain `pytest … | tail -n && git commit`.** `tail` exits 0 regardless, so the commit runs
  even when the suite fails. That let two broken commits through on 2026-07-30.

---

## How to run it

```bash
cd ~/job-search-app
set -a && . deploy/.env && set +a
export DATABASE_URL="postgresql+psycopg://jobsearch:${POSTGRES_PASSWORD}@127.0.0.1:5432/jobsearch"
```

Full runbook in `deploy/README.md`. Nothing sets `DATABASE_URL` for host-side scripts — the
containers get it from compose and a shell does not.

**Verify before pushing, not after.** A throwaway Postgres is what catches migrations and the ~290
DB-backed tests that otherwise skip silently:

```bash
docker run -d --name js-test-pg --restart unless-stopped \
  -e POSTGRES_PASSWORD=test -e POSTGRES_USER=jobsearch -e POSTGRES_DB=jobsearch \
  -p 55432:5432 postgres:16
DATABASE_URL="postgresql+psycopg://jobsearch:test@localhost:55432/jobsearch" alembic upgrade head
DATABASE_URL="postgresql+psycopg://jobsearch:test@localhost:55432/jobsearch" pytest tests/ -q
```
