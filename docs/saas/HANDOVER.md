# Handover — 2026-07-31

Where the platform actually is, what is left, and the decisions a new session would otherwise have
to re-derive. `CHANGELOG.md` is the history; this is the position.

---

## Live right now

| | |
|---|---|
| Site + API | `https://mahoraga-app.rubenlekli.com` (Cloudflare Tunnel; port 22 is the only public listener) |
| Box | `ruben@169.58.16.241`, checkout at `~/job-search-app` |
| Running | postgres, api, **setupagent**, capture, labeller, scorer, notifier, trainer, docgen, cloudflared, plus the separate embedder stack (tei + gateway) |
| Timers | **six installed and enabled** — harvester 15 min, notify-dispatch 60 s, **linkedin 15 min**, linkedin-describe hourly, health, backup |
| Tests | 1,014 passing, 2 skipped, against a throwaway Postgres |
| Accounts | `lekliruben@gmail.com` (full profile, model v2 active), `andraslekli@gmail.com` (config + model v1 active) |

`sudo deploy/install-units.sh` installs and enables every timer idempotently, and `check_health.py`
**pages** when one is missing — a timer that was never installed is invisible from inside the
application, which is how `notify-dispatch` was absent for weeks while the stack looked healthy.

---

## The thing most likely to mislead a new session

**A re-embed is half done, and until it finishes the models are trained on the wrong vectors.**

The corpus moved from embedding `description` alone to the reference's `title_desc` on
2026-07-31, which invalidated every existing vector by design (the mode is part of
`EmbedderVersion.version`, so a stale row is refused rather than silently mis-scored). At the last
check: **3,136 of 6,601 rows re-embedded, 3,465 pending, one version present.** Both users' active
models were fitted on the old vectors and cannot honestly be retrained until the rebuild lands.

Run it with `--embed-only` (it takes no advisory lock, commits per 64-row batch, and survives a
deadlock or a timeout by leaving the batch pending). Do **not** conclude from a quiet log that it
is wedged — that was true before per-batch commits and is the reason they exist.

**The previous version of this section said the JobSpy tier was not enabled. That is now wrong.**
LinkedIn, the ATS boards, the VC boards and the European boards all run: 24 hours of intake shows
linkedin 212, arbeitnow 434, himalayas 200, hiringcafe 167, nofluffjobs 87, profession 53. The
per-user LinkedIn pass exists and is on its own 15-minute timer.

---

## Order of work agreed with the owner (2026-07-30)

**The site is finished before any prompt is touched.** Prompts are measurable now and can wait; a
half-working product cannot be evaluated at all.

### 1–3. The web layer — **done, deployed, verified in a browser**

The jobs table (columns, filters, search, pagination, server-side salary and applied state), the
setup chat's typing indicator and openable file cards, the mahoraga design system across every
page, the landing page ported in with auth CTAs, onboarding as numbered step circles, a
**Your details** page where everything onboarding collected can be corrected, and delivery
settings (message interval, freshness window down to one hour).

Auth is complete and proven end to end in a real browser: signup → emailed link → verify → login,
and forgot → emailed link → reset → old password refused → new one works. Password change lives in
settings. `CHANGELOG.md` 2026-07-30 carries the six bugs that run found, three of which no API
test could see.

A `jobs_detail` page — click a row, see everything about that posting — remains deferred.

### 3b. The evening batch — **done and deployed** (2026-07-30)

Notifications (bell, collapse-onto-unread, producers on every real event), a status readout on the
Overview derived from the data, the chat rebuilt as a chat with markdown rendering, **a Model page
that owns training** so it is no longer confused with the profile chat, Telegram channel routing
proven by a test post with @getids walk-throughs, credential reveal behind the account password,
the history as one replaceable document, a delivery on/off switch, the landing page rewritten for
the hosted product, a fetched-at column, and the loading flash removed.

Verified in a browser on the live domain. Both users' bots answer `getMe`, webhooks are set with
no errors and nothing pending, and the `<1h` age element renders as the reference does.

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

- ~~**The JobSpy tier does not exist.**~~ **Built and running since 2026-07-31**, along with the
  right source list. What that measurement actually found is still worth keeping: the old enabled
  set produced 154 fresh jobs in six hours with **none** at or above the cutoff, because all seven
  were remote-work aggregators the reference does not use. Two things were wrong at once — no
  LinkedIn ingestion *and* the wrong boards.
- **The re-embed is unfinished, and the retrain behind it has not run.** 3,136 of 6,601 rows on the
  new `title_desc` vectors. Both active models were fitted on the old ones. This is the next thing
  to finish, and nothing downstream of scoring can be judged until it is.
- **The new classifier has never trained on real data.** `eebeaee` replaced a fixed Ridge
  regression with a four-way bake-off and a tuned cutoff; it is tested, and no live model has been
  produced by it yet, because that needs the re-embed first.
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
- **Phase 5 of `REBUILD_PLAN.md`** — retiring the schema machinery — is **part done**.
  `libs/templates/cv.py` and `services/docgen/docprompt.py` are gone (2026-07-31); `rubric.py`,
  `facts.py`, the whole `services/capture` + `process` lane + `ProcessPage.tsx` flow, the
  confirmation form and the generated types remain. That flow is *superseded but wired end to end
  and passing its tests*, so retiring it is a product cutover, not a cleanup — see the 2026-07-31
  changelog entry, which records why a dead-code scan deliberately left it alone.
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

- **`PUBLIC_URL` must be set on the box**, in `deploy/.env`. Every verification and reset link is
  built from it; absent, they point at `localhost` and signup looks like it worked. Now
  `:?`-required in compose, so a box missing it fails to start rather than mailing dead links.
- **Run the `service-deps` check before deploying a new import.** Installing one service's
  requirements alone into a clean venv and importing its entrypoint is what catches an indirect
  dependency — the notifier crash-looped on `pydantic` this way, and CI would have caught it after
  the push rather than before.
- **The web layer needs a real browser to verify.** Three auth bugs shipped green through the API
  suite because the failure was in what the *page* sends and how many times it sends it. The
  Playwright run that catches them is in the scratchpad, not the repo — worth committing.
- **A new route needs a line in `web/vite.config.ts`.** The dev proxy lists paths explicitly, so a
  new API prefix returns `index.html` in development and the page fails on JSON that is HTML.
  `/setup` cost an afternoon this way; `/notifications` and `/status` were caught by the browser
  pass in minutes. Add the prefix in the same commit as the router.
- **The workers log to files, not stdout.** `docker logs` is empty for notifier, trainer, capture
  and labeller because `LOG_DIR` sends them to `~/job-search-app/logs/*.log`. A quiet
  `docker logs` is not a dead worker; read the file.
- **One pending manual step:** `scripts/move_md_documents.py` moves the migrated profile `.md`
  files out of both accounts' Documents into the profile store. Written and committed, not yet
  run — the sandbox refuses remote writes to the live database.

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
