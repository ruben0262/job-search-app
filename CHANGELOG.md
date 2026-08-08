# Changelog

What changed, where, and **why** — the last one is the point. A diff already records what changed;
this file exists so that six months from now the reasoning behind a decision is recoverable without
archaeology.

Conventions (carried from the reference instance, `docs/saas/CODE_STRUCTURE.md` §7):

- **Non-trivial change → an entry here.** Formatting and typo fixes do not need one.
- **Major change → update the relevant doc in the same commit**, and say so in the entry.
- Newest first. Dates are the day the work landed.
- Per-folder state lives in that folder's `README.md` under **Current state**; this file is history,
  those are position. Deliberately not duplicated — two copies of the same fact drift.

---

## 2026-08-08

**The credential blocks read field → what is attached → how to get it, and the buttons say which way
they go.** Front end, plus the one route the delete button needed. Onboarding and Your details share
these components, so both pages moved together.

**The Save button was invisible until you typed.** `.advance` is `opacity: 0` until a form marks
itself ready, and `CredentialForm` used it — so the bot token, the Claude key and the proxy URL each
had a submit button that could not be seen from an empty field. There is nothing to fade in *to*
here: the field is the whole form. Now an ordinary `secondary` button, present and disabled, matching
**Connect my chat** beside it, and labelled **Connect my bot** for Telegram.

**Order, per block:** the field, then what is already attached, then the how-to fold. It was the
reverse, which put a fold of instructions in front of somebody who already had the value in their
clipboard.

**The chat and channel IDs are rows, not a sentence.** They were a green line of prose, copyable only
by selecting the text. Both are now the same `.stored` row the secrets use. An id is not a secret, so
copying one costs no password. Deleting one does cost something, so it is confirmed the way a
credential is.

- **New: `DELETE /credentials/telegram/chat`.** The channel already had one; the chat did not, so the
  delete button had nothing to call. It clears the id *and* drops `telegram_status` back to
  `unconfigured` — a row still claiming "connected" with no chat is the silent failure, so the test
  asserts both together rather than the id alone. No fallback, unlike the channel: removing the chat
  stops commands and CV delivery until another is connected, which is what the confirm text says.

**Red removes, green adds, the tick is a yes.** The delete control was `−` in `--muted`, the same
weight as the copy button beside it: two grey glyphs, one of them destructive. Removal is now a cross
in `--danger`, adding a row a plus in `--score`, and the copy-confirmation tick turns `--score`.
Every credential and the links list, not only Telegram.

**Removed: "Read it for me".** The button that re-read the chat id from `getUpdates` after pressing
Start. Saving a bot token already captures the chat id on the same path (`put_credential`) and the
box beside it takes the id directly, so it was a third way to set one value. `POST
/credentials/telegram/connect` is left in place and now has no caller.

---

## 2026-08-07 (late)

**A CV is rendered once, a dead posting stops costing anything, and the Build button answers
faster.** No prompt, model or threshold was touched.

**One Chrome launch per document.** Measured on the live box: a Chrome launch is **17.5 s cold and
~6 s warm**, and a build did **two or three** of them — a probe render to count pages, and then a
final render of *the same bytes*. Now every check that can be answered from the markup is answered
first, the document is rendered once, and the file that gets delivered is the one that was measured.
A CV that fits is one launch; only a CV that came back too long is two. **~6 s off every build.**

**The revision rate is now counted.** `docgen.built` carries `revised`, and `BuildResult.revised`
with it. Each revision is a second frontier call and a second render, so how often it fires is the
number that says whether the fix belongs in the prompt — which is the owner's call, not this
commit's.

**A posting that has closed stops costing us.** The describe fetch was always going to happen, so it
now reads whether the posting is still live off the same response — `404`/`410`, or the page saying
so — and marks the row closed. Deliberately not `403` or `429`: a board refusing us is not a posting
ending, and treating it as one would hide live jobs. This works for every source now; before today
only the LinkedIn metadata pass detected it.

And a closed row is no longer re-fetched or re-embedded: `awaiting_description` and
`awaiting_embedding` both had no `closed_at` condition, so a posting we knew was dead still cost a
proxy fetch (the user's own metered bandwidth) and ~0.37 s of the shared embedder. `docgen`'s
pre-build refresh reads the column before it spends a fetch, too.

**The Build button answers faster.** Two things sat between the tap and "Building your CV":
`ProfileFiles.is_ready()` loaded **every profile file — ~180,000 characters** — to compute one
boolean, inside the webhook Telegram was waiting on; and the docgen worker then idled up to **5
seconds** before claiming the job. `is_ready` now reads the one file the answer depends on
(`context.md`, which is exactly what `grounding` refuses on) and the interactive lanes — `docgen`
and `setup` — poll at 1 s via `INTERACTIVE_IDLE_SLEEP_SEC`. **~5 s off the tap.**

Deliberately *not* the `filled` column, though it would have been faster still: it is derived, it
has drifted in production before (`setup_routes` repairs it on page load), and a stale flag would
refuse a build for somebody whose profile is finished.

**CLAUDE.md §3a: prompts are sacred.** Written down at the owner's instruction — no prompt change
without explicit confirmation, covering `libs/prompts/`, `libs/agents/*.md`, `libs/scaffolds/` and
every per-user file (`base_cv.html`, `tailor_cv.md`, `tailor_cover.md`, `label.md`, …). Those are
tuned per person for the quality of their CV, and the label template's hash is every user's
`profile_version`.

Gates: `lint-imports` 2 kept; `ruff check .` clean; `alembic upgrade head` from empty; **1,371
passed, 22 skipped**; contract unchanged; `npm run build` clean. The render-count, closed-posting
and check-ordering tests were each verified to fail against the code that made them necessary.

## 2026-08-07 (night)

**Removing the age check from delivery released a 19-day-old backlog, and this is the guard that
replaces it.** Delivery still filters nothing. The queue is *maintained* instead.

What happened: the delivery query's freshness condition went on the owner's instruction — a posting
is judged once, before scoring, and everything after executes. Correct, and it left one gap: nothing
between scoring and sending looks at age, so a queue that stops draining keeps ageing. There was
already an 832-row queue behind the old per-run cap, **every row more than three days old and the
worst 37 days**, and on deploy it began going out. 92 messages at a mean age of **19.4 days** before
the lane was paused.

The two mechanisms the owner asked about were both correct, and the numbers say so: in the hour
after the deploy the score lane admitted **542 rows, all under 24 hours**, and the harvester's 5,047
rows over 24 hours had a single posting older than three days. Neither was implicated.

So: `user_job_state.retired_at`, written by the notifier's sweep once a minute. A match that ages
past `job_max_age_hours` while waiting is one the lane failed to send in time — sending it late is
worse than not sending it — so it is written off, counted, and logged at WARNING with the account
and the window. Delivery skips retired rows and is otherwise untouched.

`retired_at` rather than `notified_at`, deliberately: a row we failed to send is not a row we
delivered, and writing one as the other would corrupt every delivery count on the platform. The
pipeline panel's `expired` now reads this column instead of re-deriving the condition — it counts
what somebody actually gave up on, which is the honest version of the number the roadmap has been
calling expired since it first counted 576 of them.

## 2026-08-07 (evening)

**The language filter has never run, and now it can** — plus the two remaining bounds from the
pipeline audit.

**`corpus.language`, read once at describe time.** Detection used to run inside
`UserFilters.unwanted`, on the description, **once per candidate per account per scoring pass** — the
same posting re-detected for every user and again on every retrain. It is a property of the posting,
so it is stored beside `country` and `workplace`, indexed, and backfilled in the migration. The
filter compares a two-letter code instead of running `langdetect` over a 20,000-character
description. `fill_missing_languages` runs once a cycle for rows described before the column
existed — no network, bounded, and it disappears when the backlog does.

**And nothing could set the languages.** `user_config.languages` was in the schema and in the
onboarding *type*, with **no control anywhere in the app** — so the set was empty for every account,
`is_allowed` returned True on an empty set, and a Hungarian mechanical engineer received German
postings he could not apply to. There is now a picker on the config step, built from the schema's own
`Language` enum, so a new code cannot exist on one side only.

**Labels are no longer bought for postings the account could never read.** `select_pool` applies
`UserFilters.unwanted_for_training` — the *content* half of the pre-score filter: language, and the
terms the user asked never to see. Not the circumstantial half: how many have applied, whether it is
on site, or how old it is say nothing about whether a posting is a good training example, and
filtering on them would thin the set for no gain. Every label is bought on the user's own Claude
credential, so a posting they can never receive is spend.

**The embed step stops on a clock.** It was bounded by `max_batches=50 × 60 rows` — a row count, so
at ~0.9 s a row it can legitimately run forty-five minutes against a unit systemd kills at fourteen.
And a killed cycle does not just lose embeddings: `describe`, the language pass and
`_enqueue_scoring` all sit *after* it and never run, silently. `HARVEST_CYCLE_BUDGET_SECONDS` (11
min) is now derived once in `libs.settings`, passed into `embed_all_pending` as a deadline, and
`test_settings_are_wired.py` fails the build if it ever exceeds the unit's `TimeoutStartSec`.

**`tests/test_settings_are_wired.py`** is new, and is the guard that would have caught the window
defect: every key in `settings.DEFAULTS` must be read by something, and must have a control in the
operator panel. Both checks pass today only because `job_max_age_hours` got both.

Gates: `lint-imports` 2 kept; `ruff check .` clean; `alembic upgrade head` from empty; **1,361
passed, 22 skipped**; contract regenerated; `tsc --noEmit` and `npm run build` clean.

## 2026-08-07 (later)

**Delivery stopped judging, and the age window became the operator's** — the owner's rule, applied:
the pre-score filter is the only place a posting is judged against an account, and everything after
it executes.

**`services/notifier/freshness.py` is gone**, and with it the pre-send liveness refresh. It fetched
up to twelve LinkedIn pages on the user's proxy *inside the notify claim*, costing 13–24 s of every
~80 s run and reserving 30 s of the claim whether it used them or not. The harvester already does
this work: `CorpusRepository.awaiting_metadata` + `describe.refresh_metadata`, on the proxied pass,
where a page fetch is the unit of work. So delivery now opens no HTTP client at all, and its send
budget is the whole claim rather than what the refresh left: **54 s → 72 s, and 14 items per run →
18** at the default four-second gap.

**Delivery filters nothing.** `candidate_conditions` is out of the delivery query and
`eligibility.is_eligible` is deleted. What a user may receive was decided once, before scoring; the
notifier ranks what is above the cutoff and sends it. `eligibility.py` is now one function, `rank`.

**The age window is a platform setting** — `job_max_age_hours`, default 24, with a control in the
admin panel beside the delivery cadence. It was `DEFAULT_MAX_AGE_HOURS = 24` in
`libs/tenant/filters.py`, reached through `section["posted_within_hours"]` — a key the 2026-08-01
migration **stripped out of every stored config**. So the list was always empty, the fallback always
fired, and every account's scoring and delivery window was a constant no user and no operator could
change, sitting next to a freshness control in the panel that governs only what is fetched.
`UserFilters.from_config` now requires the window from its caller and defaults nothing.

**One construction site for the filter**: `libs.tenant.filters.filters_for(session, user)` resolves
the operator's applicant cap and window together, for the three callers that ask (score lane, health
page, pipeline panel). They each built their own before, and the window was the thing they were all
getting wrong.

**`libs.ops.health.deliverable_window` deleted** — no consumer, and it answered from
`posted_within_hours`, which no longer exists in any stored config.

Gates: `lint-imports` 2 kept; `ruff check .` clean; `alembic upgrade head` from empty; **1,355
passed, 22 skipped**; `openapi.json` unchanged; `tsc --noEmit` and `npm run build` clean.

## 2026-08-07

**The delivery continuation stopped waiting for the dispatcher, and four thresholds got one owner
each** — the first batch out of the pipeline audit.

**A run that fills its limit now queues the rest itself.** `_deliver_again` set `users.next_run_at`
and nothing else, so the continuation depended on the dispatcher's periodic pass — which runs once a
minute. A run is ~80 seconds of work; up to 60 more were spent idle with a queue waiting, on every
continuation. It now releases its own `due` slot on the mapped row (the same move
`services/scorer/__main__.py` already makes) and enqueues directly, keeping `next_run_at` as the
backstop for the case where the enqueue collapses against somebody else's job. Measured effect on
the drain rate: 14 items per ~140 s becomes 14 per ~80 s.

**`libs.labels.POOL_SIZE` is gone.** It was 300 beside the `label_pool` catalogue entry's 300 — the
cold-start target an operator sets in the admin panel, restated as a constant nothing would update.
`pool_target()` reads the catalogue, `target_for(user)` resolves the account's own ceiling, and the
processing page now reports that ceiling rather than the platform default.

**Two thresholds that both read as "enough labels to train" were named for what they each decide.**
`services.labeller.coldstart.MIN_LABELS_TO_TRAIN` → `LABELS_BEFORE_FIRST_FIT`, **and raised from 100
to 200**: it is the point where a cold start has bought enough to be worth its first fit.
`services.trainer.train.MIN_LABELS` → `MIN_LABELS_TO_FIT`, still 25: the floor below which a fit is
refused outright. They are different questions, they cannot import each other, and reading either
name told you nothing about which one you were looking at.

`ready_to_train` also clamps to the account's own `label_pool`. An operator lowering that below 200
would otherwise leave the account permanently one label short of a model it is not allowed to buy
its way to — no error, no page saying so.

**Two dead constants deleted.** `services.notifier.eligibility.DEFAULT_APPLICANT_CAP` had no reader
(the applicant cap moved to the pre-score filter; the test that looks like a user imports the `libs`
one under that alias). `services.notifier.deliver.MAX_ITEMS_PER_DIGEST` was a "10 per digest" rule
that has not been in force since `items_per_run` went unlimited — `deliver` always passes a computed
limit — so `pending_feed` now requires one.

Three new tests, each failing against the code that made it necessary: the continuation is *queued*
rather than merely due; an account allowed fewer labels than the constant can still train; and the
label-pool default is read from the catalogue rather than restated.

Gates: `lint-imports` 2 kept; `ruff check .` clean; `alembic upgrade head` from empty; **1,358
passed, 22 skipped**; contract regenerated with no change to `openapi.json`; `npm run build` clean.

## 2026-08-06 (late night)

**One line per comment, across the whole tree** — the owner's instruction. Every module, class and
function docstring is now a single sentence, every comment block is one line, and the reasoning that
used to sit above a constant now lives where it is already recorded: this file, the roadmap, and
`git blame`.

The scale of it: **20,100 lines removed, 6,900 added, across 313 files** — `libs/` and `services/`
first, then `tests/`, `deploy/scripts/`, `scripts/`, `web/src/` and `web/scripts/`. Comment and
docstring lines were 39% of `libs/` before this.

Nothing about behaviour changed, and two checks say so rather than one:

- Every non-docstring string literal in every changed Python file is byte-identical before and
  after — prompts, log messages, SQL, error text. The one file that differs is `ats.py`, and that is
  the dead code below.
- `web/dist/assets/index-CYtWZgGD.js` — **the same content hash as the build before the pass.** The
  bundle is byte-identical, which is what a comments-only change to a TypeScript app should produce.

**Three things deleted for having no consumer** (`services/harvester/sources/ats.py`, 46 lines):

- `harvest_from_urls` — recorded ATS tokens found in apply-URLs from any source. Nothing has called
  it since the port. The place it would plug into now is `services/harvester/describe.py`, which
  resolves `application_url` on every described posting; if that is wanted, this is the function to
  restore rather than rewrite.
- `extract_tokens`, `_URL_PATTERNS`, `_NOT_A_TOKEN` — the pattern table `harvest_from_urls` read,
  and its only caller. Dead with it.
- `FETCHERS` — an ats-name → fetcher map with no reader. The `gather_*` wrappers pass
  `fetch_company=` explicitly, so this was a second copy of that routing table that nothing
  consulted and nothing would have failed on if it had drifted.

**One dead test double deleted** — `DatingClient` in `tests/test_ranking.py`, 18 lines of stub
client that nothing constructed. The section it belonged to now tests dating through
`postings.parse_posted_at` directly.

**Four tombstone comments deleted** — "`fetch_easy_apply` was removed on 2026-08-05", "`profile_context`
was here", "Location is not scored here any more", "Removed 2026-08-05, and the removal is the fix".
A note about something that is gone is the same maintenance burden as the thing itself; the removals
are in this file and in the history.

**Not touched: the ported source adapters** (`services/harvester/sources/{http_apis,hiringcafe,jobspy_source}.py`).
They are lifted from the reference instance and deliberately kept diffable against it — the reason
`pyproject.toml` exempts that glob from E501. They were slimmed and then reverted; `ats.py`,
`base.py` and `__init__.py` in the same folder are this platform's own code and were kept slim.

**Not touched: `libs/db/migrations/versions/`.** A migration is a record of what was applied, which
is why `pyproject.toml` already excludes it from ruff.

Gates, locally, all six: `lint-imports` 2 contracts kept; `ruff check .` clean; `alembic upgrade
head` from empty on a throwaway database; **1,355 passed, 22 skipped** with `DATABASE_URL` set;
the OpenAPI dump and `api-types.ts` regenerated — 72 endpoint descriptions shortened, **no type
changed** (every line of the 482 removed from `api-types.ts` is a comment); `tsc --noEmit` and
`npm run build` clean.

## 2026-08-06 (night)

**One filter, once, before scoring** — the owner's instruction, and it turned out to be the cause of
three separate symptoms.

The applicant ceiling, workplace mode, language and the freshness window all ran **at send time**, in
`services.notifier.eligibility`, while the pre-score filter knew only about countries, freshness and
excluded words. So a posting an account could never receive was still embedded against their model,
scored, counted as *waiting* by the health page and by the radar — and then dropped in the last
second before sending, by a filter neither of those pages knew about.

That is one question with two answers, which is the defect this project has shipped more than any
other, and it produced:

- **A permanent health alert.** *"1 matching job waiting and last delivery was 2h ago"* on the second
  account, every cycle. The notifier's own log said it: `dropped {'workplace': 1}` then `notified
  with 0 item(s)`. The count and the send disagreed, and the count was the one an operator read.
- **A delivery queue that would not drain.** 395 rows above cutoff, of which `dropped
  {'applicant_glut': 51, 'excluded_keyword': 4, 'workplace': 2}` every run — the queue was mostly
  postings that were never going to be sent, so the radar's "waiting" was counting work that did not
  exist.
- **Wasted model calls**, on every posting the account had already ruled out.

All four checks now live in `libs.tenant.filters.UserFilters.unwanted` and run in the score lane, so
a row that fails them is skipped before the model sees it and never counted as waiting. Delivery has
one check left — `closed`, learned from the posting's own page seconds before sending, which is the
only thing no earlier filter could have known.

`Eligibility`, `is_eligible`'s preference half and `too_old` are deleted in the same commit, and the
tests that covered them moved to the layer the behaviour moved to rather than being dropped — the age
window's tests are now against a real database, because it is a SQL condition now.

**`scripts/rejudge_scored_rows.py`** re-applies the filter to rows scored under the old arrangement.
Without it those keep their score forever: the score lane never revisits a row already carrying the
current model version, so the alert would have outlived the fix.

**And a bug the suite caught in this change**: `UserFilters.from_config` read `applicant_cap=None` as
"not supplied" and substituted the catalogue default — turning an operator's deliberate *Unlimited*
back into 200. `tests/test_limits_are_wired.py` exists for exactly that pattern and found it before
it shipped.

---

## 2026-08-06 (evening)

**Nothing stands between a scored match and Telegram any more**, on the owner's instruction — and
the measurement agreed with them.

**Corrected after deploying — the cause was the wait, not the ceiling.** `items_per_run` defaulted
to 10, which is 960 a day, and that default is wrong for the reason below. But both live accounts had
it explicitly set to `null`, so `resolve` was already returning Unlimited and the ceiling was never
what held them back. The evidence is the throughput shape: raising the default changed nothing, and
re-arming the run took delivery from ~18 every fifteen minutes to ~18 every fifty-five seconds —
**about 24×, measured at 950 an hour against 40 before.** The default is unlimited now anyway,
because 960 a day is below what an account matches and a new signup should not inherit that.

**What actually cost the 576:** A run stops at its claim so a second worker cannot
start the same digest — that stays, it is the fix for the flood-then-silence incident. What was wrong
was what happened next: the remainder waited a full fifteen-minute cadence, so a backlog drained at
one run's worth per cycle while its tail aged past the freshness window. A run that could not finish
now makes the account due immediately and the dispatcher already inside the worker picks it back up.
No second mechanism and no new clock.

`UNLIMITED_ITEMS = 200` is gone. It was a number with no relationship to anything — the shape of a
fallback that swallows a real answer (CLAUDE.md §5). What a run can fetch is now derived from what it
can physically send: its budget divided by Telegram's gap.

**The freshness complaint turned out not to be a filter fault, and the measurement is the answer.**
LinkedIn rows are fetched at **0.6 hours old on average** under the source's own 1-hour window, and
25 of 5,626 lack a `posted_at` at all. Jobs arriving at three hours old had spent **191–207 minutes
in the delivery queue** — the posting was fresh when we found it and stale when we sent it. Same
cause as everything above.

**And one bug in yesterday's radar: the queue has no state called `running`.** It is `claimed`, so
the live view's one moving number counted zero forever — a page built to show whether work is moving,
whose "moving" could not move. Found by reading it against the table the same afternoon.

Measured while checking whether any of this is slow, since it was asked: the per-user filter is
**76–280 ms of SQL** over 30,441 corpus rows and **1.7–3.6 ms of Python** for 500 rows, the score
lane averages **1.39 s** a batch (worst 12.1 s), and notify averages **24.4 s** (worst 82.3 s)
against a 120-second claim. Nothing here needs optimising; the numbers are recorded so that claim can
be checked rather than believed.

---

## 2026-08-06 (later)

**The radar's first day found three of its own numbers lying**, which is roughly the point of
building it — read against the database rather than trusted, per CLAUDE.md §4.

- **The delivery queue's age described a posting the count had excluded.** The count carried the
  account's filters and the age did not, so the page read *142 waiting, oldest 138 hours*: two
  individually correct numbers that together described nothing. They now share a condition list.
- **The scoring queue's age was never computed at all** — initialised to `None` and never assigned,
  so whatever piled up behind the score lane the row stayed green. The one colour that cannot fire
  is the one nobody notices is missing.
- **"Gathered" and "Stored" were one `INSERT` drawn as two stages.** `fetched_at` and
  `first_seen_at` are set by the same statement, so nothing is ever queued between them and the two
  counts could only ever be equal — except across 2026-08-04, before which `fetched_at` did not
  exist, where the page showed *fewer gathered than stored*. Now one row (CLAUDE.md §6).
- **A stage with no queue said `0` rather than a dash**, breaking the page's own rule that zero is a
  claim and a dash is an admission.

**Delivery is now judged on lost work rather than on queue age**, and the measurement changed the
answer. `items_per_run` caps sending at ten a cycle *on purpose*, so a queue behind it is the design
working and an age-based colour would be red every day of the platform's life. What is genuinely
wrong is a posting the model matched, that nobody was ever shown, that is now too old to send —
**measured live at 576 in 48 hours for one account**, against 931 delivered. The cap is below that
account's own match rate, so the tail ages out; the page now says so and names the setting.

**The pipeline streams.** `GET /admin/pipeline/stream`, server-sent events at one second, and the
page shows which counts moved. Polling was right first — it proved the numbers before anything was
built on them, which is how the three above were found — but the platform works in bursts, and
fifteen minutes of quiet followed by a thirty-second drain is exactly the interval where "moving"
and "stuck" look identical unless you can watch it. SSE rather than a websocket: one-way text that
reconnects by itself and needs no library on either side. The queue's own lanes are beside it, which
is the only place a dead-lettered job is visible at all — from the outside, work abandoned and work
never enqueued look like the same empty queue.

**The contract gate failed for a reason that had nothing to do with the contract.** `dump_openapi.py`
let Python pick the line ending, so a dump run on Windows committed CRLF and CI regenerated the
identical document with LF — a 4,642-line diff in which no line had changed. Same defect as
`alembic>=1.13` (ROADMAP 6al): **a gate whose result depends on which machine ran it is not a
prediction of CI, it is a coincidence.** The writer now pins `newline="
"` and a test fails the
build if a CRLF contract is ever committed again.

---

## 2026-08-06

**Where the jobs are, and what the filter removed** — the two numbers nobody was reporting.

The pipeline radar (`libs/ops/pipeline.py`, `GET /admin/pipeline`, an Admin tab) is one row per
stage with how many are waiting in it and how long the oldest has waited. Every outage this platform
has had was a stage quietly stopping while everything around it looked fine — six hours of ingestion
behind an invisible filter, LinkedIn dead for five hours behind a timer that could not fire, 229 rows
stuck awaiting an embed while both health alerts pointed at delivery. **The number existed every
time; reading it meant knowing which query to run.**

Every count is `SELECT count(*)` against the condition the *next* stage actually reads, so a stage
cannot report empty while work sits in it. Where a number is genuinely unavailable it is null and the
page shows a dash — zero is a claim, a dash is an admission. Verdicts are about backlog *age*, not
size, and derived from the tick meant to clear the stage: a thousand rows moving is healthy and forty
stuck for a day is not, and colouring by size would turn a busy platform permanently red.

**The embedder could not have succeeded.** Measured against the live backend: 16 texts in 17.1s, 32
in 26.8s, 64 in 52.1s, 128 in 107.8s — flat at ~840 ms a text. `EMBED_BATCH_SIZE` was 200, needing
~168 seconds against a 120-second client timeout, and the backlog is what produces a full batch, so
the timeout preserved the condition that caused it. Now derived from the timeout with the measured
rate named beside it, and pinned by a test in `test_schedules.py`.

**What the pre-score filter removed** (`filter_effect`, on the latency page) is the roadmap's stage-3
row, and deliberately not a duration: the filter costs microseconds a row, so timing it would report
noise. Per account, because one user whose filter rejects everything is invisible in a platform
total — and a feed silenced by a tight filter looks exactly like a feed with nothing new, which is a
failure this project has now shipped twice. Judged at one end only: everything removed is red,
nothing removed is cold start and is not.

**Stage 2's planned latency row was not built, and the roadmap now says why.** Country extraction
turned out to run inside the same `INSERT` that sets `first_seen_at`, so the span is zero by
construction. A row for it would be a permanent green measuring nothing, which is worse than no row.

---

## 2026-08-05 (later)

**The CV and cover-letter buttons did nothing, and the cause was general** (ROADMAP 6af). Reported
on the second account, with the instruction that made the fix right: investigate the *setup*, not
the account. It was general — the first account worked only because its database row had been
filled in by hand.

### A column with no producer (`services/api/credential_routes.py`)

`users.telegram_webhook_secret` was declared on the model, created by a migration, and read by the
webhook route to identify whose bot a tap came from. **Nothing in the tree ever wrote it.** Probed
live: both bots had a webhook registered with Telegram, one had the matching secret on its row, and
every tap on the other matched no user and 404'd.

Connecting a bot now does both halves — the secret on the row, and `setWebhook` on that user's own
bot pointing at `PUBLIC_URL/telegram/webhook/<secret>`, with the same value as Telegram's
`secret_token` header so a forged POST to a guessed path fails. Idempotent, so pressing Connect
repairs any account that predates it. Best-effort: a Telegram outage must not strand somebody
mid-onboarding, and the secret is stored either way.

**The 404 was silent, which is why this survived weeks.** A tap matching no account now logs at
WARNING — that line is the only symptom the platform ever gets.

### Four definitions of "jobs per day", and the home page showed the wrong one

The user config said 20, `status_routes` mirrored a constant of 100, `deliver` held a third at 100
in a parameter **nothing read**, the plan quota held a fourth, and the notifier enforced none of
them — it reads `libs.limits`, where the default is unlimited. So the dashboard told an account *"At
most 20 messages a day"* on a day it received **908**, and a test was keeping two of the fictions
equal to each other.

One reader now, and `None` stays `None` rather than becoming a number that understates what the
platform sends. The dashboard renders "No daily limit on messages"; the status page claims a spent
allowance only when a cap exists. Three constants and an unused parameter deleted.

### A dead letter could be read but never retired (`services/api/admin_routes.py`)

`DELETE /admin/queue/dead?lane=<lane>`, with an `×` on the banner. **The alert goes because its
cause is gone, not because a banner was hidden** — deliberately not a general dismiss, which would
let a real outage be silenced in one click. Each row's lane, owner and final error are logged at
WARNING before deletion: the row goes, the evidence does not. No retry — that decision belongs to
whoever fixed the cause.

### The per-source table's columns did not line up (`web/src/styles.css`)

Every row was its own grid with `auto` columns, so each sized its columns from its own contents:
`168h` set a wider column than `2h` and nothing aligned down the list. `auto` cannot align across
siblings — one shared grid with fixed control columns, rows as `display: contents`.

### Also recorded

The usage meter is **G9 cost visibility** in `GAPS_REVIEW.md`, still open and never built: nothing
meters spend, `libs/llm` does not read `usage` from a response, and no ledger exists. The `Quotas`
mechanism enforces `cv_builds_per_day` at the Telegram admission point; the other seven limits have
no display. Recorded rather than guessed at.

## 2026-08-05

**Both alerts on the admin health panel were real, and neither meant what it said** (ROADMAP 6ad).
The owner's observation is the one worth keeping: having them on the panel at all is what made this
findable in minutes, instead of by an account going quiet for a week.

### A Telegram outage permanently lost two digests (`services/notifier/__main__.py`)

Two dead-lettered notify jobs, both `RuntimeError: Bad Gateway`, five attempts each, sixteen minutes
apart. `send` retried only 429 — every other `>= 400` raised on the first response, so the queue
spent all five attempts inside five and a half minutes and dead-lettered while Telegram was still
unwell.

**Nothing here was broken.** The retries worked, the attempt counter worked, the dead-letter worked.
Together they sampled an outage at a resolution far finer than the outage, which is a different kind
of mistake and one that no component could have detected alone.

5xx now retries inside `send` with a backoff, before it costs the job an attempt. 4xx other than 429
still fails immediately: a malformed payload or a revoked bot does not improve by asking again. The
two conditions get **two budgets, counted separately**, so a burst of flood waits cannot consume the
allowance an outage needs. The failure message names which budget ran out — the old one said
"Telegram kept rate limiting this bot" during an upstream failure, sending an operator to look at
bot limits for a problem that was never theirs.

`tests/test_telegram_send.py`, five tests. Deliberately in their own file with no database
dependency: written first inside `test_notifications.py`, they were silently skipped by that
module's `DATABASE_URL` guard, which is the same trap CLAUDE.md §6 now names.

### An alert that could never be cleared, about a notifier doing its job (`libs/ops/health.py`)

"71 matching job(s) waiting and last delivery was 5h ago." Measured before changing anything: all 71
rows were **older than the 24 hours that account had asked for**, so `pending_feed` excluded every
one in SQL and correctly sent nothing. The lane was healthy — 657 jobs done, newest minutes old —
and the account's own log line read `dropped {'applicant_glut': 1, 'workplace': 1, 'too_old': 11}`
over a deliverable pool of exactly 13, matching the database to the row.

`user_health` counted `score >= 60` and nothing else, which was wrong three ways at once:

- **60 is not a cutoff anybody delivers at.** `cutoff_for` reads the operating point each user's own
  training run tuned; the two live accounts are at **29 and 42**. The constant counted a pool
  neither uses and missed jobs genuinely waiting below 60.
- **A closed posting is not waiting** — there is nothing to apply for.
- **A posting past the user's window is not waiting** — it can never be sent, so counting it
  restates "the corpus contains old jobs", which is always true and never actionable.

`pending_matches` now draws the same pool `pending_feed` does: per-user tuned cutoff, open, inside
that user's own freshness window. The account reads 0. Still a deliberate upper bound — eligibility
and the dating pass run over descriptions, not in SQL — because over-counting is a false alarm
somebody investigates and under-counting is silence.

**The general defect is the one this project keeps shipping: an alert that defines its trigger
differently from the code it watches is not an alert, it is a second implementation.** Written where
the number is computed, not only here.

### The logs that should have shown this were nine parts noise (`libs/obs/logs.py`)

Roughly one line in ten of the notifier's output was the notifier's. The rest was `httpx` announcing
every posting-refresh request that had **succeeded**, while the lines that mattered scrolled away.
Third-party loggers are raised to WARNING (`NOISY_LIBRARIES`) so they still report trouble and stop
narrating success — and *cleared* again under `LOG_LEVEL=DEBUG`, not merely skipped: a logger's
level is process-global and outlives the call that set it, so skipping left the trace missing at
exactly the moment somebody had asked for it. Found by a test, not by reasoning.

### Two working rules added (`CLAUDE.md`)

**§6, green before it is pushed.** The four gates CI runs — `lint-imports`, `ruff`, `alembic upgrade
head` from empty, `pytest` with `DATABASE_URL` — run locally first, in that order. A red CI run
after a push is a fix under time pressure that could have been a fix at leisure. Includes the skip
trap: ~290 tests skip silently without a database and the suite still prints green, so the skip
count is part of the result.

**§7, logs are the only view into a running system.** Every swallowed exception gets a line naming
what failed and what was done instead; levels are defined so severity means something; and the
INFO-flooding rules, because a log nobody can read hides whichever failure happened during the
noise.

### The latency panel's two red rows, checked and mostly not defects (ROADMAP 6ae)

**`Posted → fetched`, worst 54h43m: entirely HiringCafe** (p90 30.5h over 105 rows, against
LinkedIn's 1.0h over 2,333). The wiring is correct — it is asked for `posted_within_days = 1` — and
it returns 54-hour-old rows anyway, because its filter granularity is days and its day boundary is
loose. Nothing to tighten from here, which is why that row is `reported, not judged`. It is wasted
*intake* rather than wrong delivery: those rows are caught at delivery as `too_old`, having already
been fetched, described and embedded. A pre-score filter (6u/6x) is where that stops costing.

**`Described → embedded` p90 2h18m: already over.** Backlog zero, current p90 0.8 minutes; the
24-hour window is showing an evening catch-up burst that drained by 02:00. No code change — the
finding is that a 24-hour p90 stays red long after recovery and the row does not say so. Deferred.

## 2026-08-04

**Stage 1 — "the queries are right" — closed.** Eight defects found and fixed, and **not one of them
raised an error**: the query set was wired to disabled sources; the union readers read the wrong
config level, so every board on the free tier fetched its unfiltered recent feed while the platform
behaved as though nobody had signed up; eight sources were fed a query shape only two read; seven
ignored keywords entirely; the fold key was being typed into search boxes; two sources ignored the
operator's freshness window; **six of ten operator limits governed nothing**; and LinkedIn searched
3 of a user's 23 keywords.

Then, in the same day, a live outage with three independent causes — the harvester was never in
`deploy.sh` so `fetched_at` was never populated; the pre-send refresh had no wall-clock budget and
starved the notify lane's two-minute claim; and `UNLIMITED_ITEMS = 10_000` built a candidate pool
that could not be ranked inside that claim. Delivery was dead for both accounts while every job
reported `done`.

Also that day: `Unlimited` silently meant "the default" everywhere it was offered, because
`resolve(...) or DEFAULT` cannot express `None` — found from a screenshot of the admin panel where
the operator's change had visibly not taken. Location left the score and `europe` became one thing
rather than two; seven sources that cannot answer a keyword were removed; the country vocabulary
(`libs/corpus/geo.py`) and the corpus's own clock (`clock_timestamp()`, `fetched_at`,
`embedded_at`) both landed; and LinkedIn's card signals were measured against live pages rather than
reasoned about (ROADMAP 6ac).

Full detail per defect is in `docs/saas/ROADMAP.md` 6q–6ac, each with the measurement that justified
it. Recorded here late — the day's work went in under twenty-six commits and no changelog entry,
which is the gap this file exists to prevent.

## 2026-08-03

**The whole workflow walked on a fresh account** — signup, onboarding, the setup chat with its
prompts and commands, training, and delivery — which is ROADMAP 6h, and it was the right call. The
platform passed every automated test throughout and could not get a second person from signup to a
delivered job. Fourteen defects, and **not one of them was an exception**: every single failure was
a component doing something reasonable with a value nobody had written, or reading a different
column from the one that had been.

Three families, each of which is a lesson rather than a bug:

1. **Two definitions of the same thing, disagreeing.** "Profile ready", "is admin", "is a run in
   progress", "is this account due" — each existed twice, and in every case one copy was the one
   enforced and the other was the one the user could see. The visible half is what a person acts on,
   so the account looked finished and refused to work.
2. **Written here, read there.** The setup conversation replaced the capture flow and writes profile
   *files*; four consumers still read columns only the old flow ever populated. Nothing errored —
   the readers found NULL and did the sensible thing with it.
3. **Nothing arms the first value.** Delivery slots, and before them the rubric column: a field that
   only ever gets *advanced* by the thing that consumes it, with no code anywhere setting it the
   first time. The founder's account hides every one of these, because a migration script filled its
   columns before the rebuild.

### Every account created on the platform was permanently silent (`libs/queue/queue.py`)

Found by the alert, which is the first time one of these has been caught by the platform rather than
by a person noticing. `andraslekli@gmail.com`: **73 matching jobs waiting, last delivery never.**

Not the notifier and not Telegram. The bot was `connected`, the model trained, and all 15,094
postings scored. **No `notify` job had ever been enqueued for that account, and none ever would
be.** `dispatch_due_notifications` selected on `next_run_at IS NOT NULL AND next_run_at <= now()`,
and nothing on this platform ever sets that column — the dispatcher only advances one that already
has a value, and the LinkedIn loop owns a different one entirely. Signup, onboarding, and connecting
the bot all leave it NULL.

The first account works only because it was migrated from the reference instance with a value
already in it. That is what hid this: the platform looked like it delivered, because the one account
old enough to test with had been given its first slot by a migration script rather than by the
product.

NULL now means **due**, which is how `services/harvester/linkedin.py` has read the same shape since
it was written. Fail-open is the only safe direction here: a missing schedule that produces a
delivery is a delivery somebody can see, while a missing schedule that produces nothing is an
account nobody hears from and no worker is failing over. Arming the column at signup instead would
have left the same bug behind every future path that forgot to — which is how this one arrived.

Every other gate is unchanged, and a test pins that, or "due when unscheduled" quietly becomes "due
regardless of dormant, inactive, or a revoked bot".

Deployed, and the next dispatch pass delivered 9 items to that account at 15:29 UTC. The alert
cleared on the following health run.

### Where the time goes: the latency tab, one row per zone (`libs/ops/latency.py`, `web/src/pages/AdminPage.tsx`)

ROADMAP 6p. The end-to-end number is the one worth publishing and the one least useful for fixing
anything: "a posting took 36 minutes" does not say which of six handoffs ate them, and a stuck
describe worker, an embedder behind a flood, a score lane with no free worker and a delivery cadence
are four unrelated problems wearing the same number. 6n gave that number a verdict; this gives it an
address.

A posting arrives as a link and leaves as a card, so the rows are the boundaries in between: posted
→ found, found → described, described → embedded, embedded → scored, scored → delivered, and the
whole journey. Collapsed, a row says *which* zone. Expanded, it says *why that zone* — waiting
versus running from the queue's own timestamps, the backlog queued at that door, the hour-by-hour
shape, and the threshold's reasoning in words. The detail is fetched on expansion: only one row is
ever open, and running six drill-downs on a page load is six times the work to answer a question
nobody asked.

**`corpus.embedded_at` is new, and deliberately not backfilled** (migration `c4d8f1a2e7b6`). The
middle of the pipeline had no clock — a row recorded when its text arrived and then jumped to having
a vector — so described → embedded and embedded → scored were unmeasurable and both hid inside one
found → scored number that could not say which was slow. Filling the column from `first_seen_at`
would have invented a measurement, so historical rows stay NULL and those two rows read "not
measured yet" until the corpus turns over.

Two measurement rules that cost something and are worth it. **Shared work is measured on `corpus`
alone**: a description is fetched once for everybody, so counting it through the per-user join would
multiply every posting by the number of accounts holding it — a real percentile over a population
that never existed, which is worse than a missing number because it looks right. And **posted →
found is reported without a verdict**: sources round what they publish ("2 days ago"), so a
threshold there judges their precision rather than our lag. `unjudged` is kept distinct from
`unknown` for the same reason — a deliberate silence must not read as missing data.

### The training run refused, then crashed, on a profile that was complete (`libs/scoring/features.py`, `services/trainer`, `services/scorer`)

**The sixth place the same assumption was made, and the previous five were each fixed alone.**

`ProfileSchema.model_validate(user.profile)` — the JSON the capture flow wrote and nothing sets any
more. An account built by the setup conversation has prose files and no JSON, so this raised: it
killed the training run five times, and once training was fixed it killed *every score job for the
same account* instead. Both dead-lettered with a pydantic error that named a schema rather than the
thing that was actually missing.

`spec_for` is now one helper for all of it. An absent profile yields an empty spec, the keyword
features contribute nothing, and the embedding carries the whole signal — a real model, and strictly
better than refusing to score a profile that is complete in the form the platform now uses.

`services/docgen/run.py` makes the same call and will fail the same way the first time this account
builds a CV. **Deliberately not fixed here**: a document genuinely needs the person's details, so it
has to read the profile files rather than fall back to an empty schema. That is ROADMAP 6q.

### A job's finish time was when its transaction opened (`libs/queue/queue.py`)

`finished_at` was set from the statement's own clock rather than the wall clock at completion, so
every duration measured on the queue was short by however long the transaction had already been
open. Harmless until something reads it — which the latency drill-down now does, per lane, as the
number that separates "no free worker" from "the work got slower".

### Stop did not stop, and the page could not tell a run from a gap (`libs/labels`, `services/api/process_routes.py`, `web`)

Four defects in one surface, found by watching a real training run rather than by testing one.

**Stop cancelled queued rows, which could never have ended it.** The labeller re-enqueues itself
while work remains, so the deleted row came straight back and the run carried on spending the user's
own Claude credential while the page reported it stopped. A flag the worker reads before it re-arms
is the only thing that can end a self-perpetuating loop from outside it: `users.labelling_paused`,
set by Stop, checked in the one place that keeps labelling alive, cleared when the user starts again
— a pause, not a way to brick your own account.

**A run between batches read as no run at all.** Labelling goes in batches, and between a worker
finishing one and the dispatcher enqueuing the next the account has nothing queued. "Running" read
only `ready` and `claimed`, so the page flipped back to its start button mid-run and told a user
nothing was happening while ten jobs had just been judged. A recent finish now counts too, for as
long as the phase has work left. The page had the mirror of the same bug — it polled only while
running, so one idle moment ended the polling for good, and a page opened fresh mid-run never
started at all if its first answer landed in a gap.

**Nothing said the run survives the browser.** Pressing the button writes queue rows and the worker
pools drain them, so closing the laptop changes nothing — but the page said "Starting…" once and
then looked identical whether the run was halfway through or had failed an hour ago. It now derives
the phase, a count against the target, and a bar from the queue rather than from a progress column,
for the reason every other state here is derived: a counter that can disagree with the queue
eventually will.

**And the label budget an operator set was read by nothing.** `label_pool` was in the limits
catalogue and rendered in the admin panel as "Jobs bought per labelling run" — a control that looked
like a setting and behaved as decoration. It matters more than most, because labelling runs on the
user's own credential: how many jobs to buy opinions on is a decision about their money.

### A cold start stalled at ten labels because the corpus belongs to whoever harvests most (`services/labeller/pool.py`)

The candidate window was the newest 5,000 rows **on the platform**, filtered against the user's
config only afterwards. The corpus is shared, so recency belongs to whoever searches most: an
account looking for mechanical engineering roles had 159 matching rows sitting in the corpus and saw
none of them, because thousands of fresher software postings stood in front of them. Ten labels, a
warning in a log nobody reads, and a page reporting "10 of 300" as though it were progressing.

The user's own terms now narrow the query before the budget is spent. A share of the newest rows is
kept alongside regardless of terms, because the rules score reads location and remoteness too and a
pool drawn only from title matches would never surface a job worded in a way the user did not think
of.

**Not the harvester's query budget**, which was the first suspicion and turns out to be already fair
— `select_within_budget` is round-robin with carry-over, so a niche search gets its first query
before anyone gets their second. An earlier remark blaming the 24-hour freshness window was
speculation and was wrong: `score_job` has no age term and the pool looks back 90 days.

### Setup writes one place and three consumers read another (`services/api/setup_routes.py`, `process_routes.py`, `libs/profiles`)

The family that cost the most, all three found on one account in one afternoon.

**The rubric never reached the labeller.** `label.md` is written as a profile file; cold start,
relabelling and label scoping all read `users.rubric_md`, which only the superseded capture flow
ever populated. So an account with a complete 13,809-character rubric had a labeller answering
`confirm_profile` forever — no pool, no labels, no model, and nothing anywhere saying why. Writing
`label.md` now mirrors it onto the user row and re-derives `profile_version`, which is
`sha256(rubric_md)[:8]` and scopes which labels a training run may use, so it has to move with the
text. `profile_generation` advances only when the rubric actually changed, or an identical rewrite
would send somebody back through relabelling they did not need.

**A finished profile was offered the flow that would overwrite it.** An account that had just
completed setup was told to "process your documents to build your profile" — the capture flow the
conversation replaced, and running it would have overwritten a finished profile with a
schema-derived one, the exact regression `REBUILD_PLAN.md` exists to prevent. Two definitions of
"profile ready" that disagreed: `may_train` and the setup page read the files, this read
`processed_at`, a column nothing sets any more.

Found while checking a live account was ready to train, **not by a test** — which is the argument
for checking before pressing the button rather than after.

### The director handed the user back to itself (`libs/agents`, `services/api/setup_routes.py`)

A live run ended by telling the user to open two more chats — for role angles and the scoring rubric
— which that same conversation was carrying the instructions to do.

Two causes, and the second is the real one. The director's body said it was not handing off; the
environment block appended *after* it said the opposite, with the exact sentence to use. **Last word
wins, and the last word was wrong.** The handoff paragraph is now a slot in that block: the four
standalone steps still get it, the director gets the opposite instruction.

But no wording would have held, **because after the first turn it was not the director**. Only a
message opening with a slash command names an agent; every ordinary reply omits it and the field
defaulted to `setup`. So a chat started as the director ran the director exactly once and handled
every subsequent answer with a prompt that knows nothing about the four-step map and is told,
correctly for itself, to send the user elsewhere for anything it cannot do. That is also why the
handoff always arrived at a step boundary. **A conversation is one command from end to end** — the
assumption the whole design rests on, and the one thing that was not enforced. The agent is now
resolved from the conversation, per conversation, so redoing one step in a second chat cannot
retarget the first.

### The setup page withheld its own good news (`web/src/pages/SetupPage.tsx`)

Progress was read on mount and when a message was sent. The turn that writes the last file is by
definition one nobody sends a message after, so "setup complete, your model can be trained" waited
for a reload — the single moment this page has good news and it was the moment it stayed quiet. It
now re-reads the moment a turn stops running.

Chat titles were generated and only generated, so three chats called "Your profile" told nobody
which was which. A pencil beside the delete cross renames in the tab itself; auto-naming only ever
fills a chat still holding the default, so a renamed one keeps its name.

### The operator can now see the corpus, and seed it (`services/api/admin_routes.py`, `web`)

ROADMAP 6n, first two pieces. A new user's training data is whatever the shared corpus already holds
for their field, and the corpus only holds what previous users searched for — which is how the
second account found 240 matching postings against 14,359 rows. Until now the only way to look was
SQL, so nobody did.

A Corpus tab searches every posting with source and date facets, and operator keywords inject terms
into the query set to fill the corpus ahead of the people who will need it. **Stored separately from
user keywords and visibly distinguished**: an operator may delete their own and must never be able
to delete somebody's real search term, which is theirs and is what their feed depends on. Both lists
are searchable and paged — twenty-odd terms in an unordered grid reads as a wall.

Also fixed on the way: **a trained account was permanently "training"**, because the state was read
from the presence of a model rather than from the queue.

## 2026-08-02

### Onboarding could not be completed at all (`web/src/pages/onboarding/`, `services/api/onboarding_routes.py`)

Three separate walls on the signup path, each of which stopped a real person.

**The chat id could only be discovered, never supplied.** It was captured by reading whoever last
messaged the bot, which needs the update to still be queued at Telegram and nothing else to have
consumed it — neither of which the platform controls. When it came back empty there was nothing else
to try: the page stayed incomplete and its only instruction was to press Start again, which the user
had already done. The id is not optional — delivery returns early without it even when a channel is
configured — so there is now a field for it, proven by *messaging* the chat rather than by looking
it up, because a bot can read a chat it may not write to and writing is the only thing delivery
needs. Auto-capture stays as "Read it for me"; what it can no longer be is the only way through. The
bot-token hint promising "you never have to find it" is gone, being a promise the platform cannot
keep.

**Moving forward was a side effect of saving.** Nothing on the page moved you on, so a step already
finished — revisited from the rail, or completed a moment earlier — had nothing to press, and a step
that saves twice could disappear part-way through. Telegram is the clearest case: it takes a token,
then wants the chat id, and the page had already gone. Continue is now always present and disabled
until the step is satisfied. That is a control you can see before you can use it, which the previous
design deliberately avoided; it is the lesser problem, since a greyed button explaining itself beats
an absent one somebody hunts for.

**The locations cap was five, and the radios were invisible.** The picker offers about twenty
choices, so any honest answer to "tick everything that applies" went past the cap — and the only
thing that said so was a validation failure after the tick, naming a count rather than a choice.
Twelve now, counted as the form fills. `accent-color: var(--ink)` painted the selected dot near-white
on a near-white control; checkboxes survived it because a tick reads as a shape at low contrast and a
dot does not.

### One admin rule, in three copies, disagreeing (`services/api/dashboard_routes.py`)

Granting operator access from the panel set `is_admin` and the API began accepting that user's
`/admin/*` calls straight away — while their dashboard carried on hiding the menu that leads there,
because it computed its hint from `ADMIN_EMAILS` alone. **Access with no way to reach it**, which to
the person granted it is a toggle that does nothing. The onboarding branch omitted the check
entirely, so an operator part-way through their own signup saw no panel at all — exactly when they
want one, since a fresh account being set up is usually the thing they are watching.

Both branches now call the predicate `require_admin` enforces. The two sources stay ORed, so a flag
can grant access but cannot revoke the bootstrap operator.

### The CI check that would have caught two crash-loops existed, and the new service was not in it

The OAuth broker crash-looped twice in production on transitive dependencies of `libs.obs` —
cryptography, then sqlalchemy — each found by watching a container restart. Every other service has
a job proving it starts from its own requirements alone; the broker was added without one.

Also green again: the committed API contract was missing three credential routes, and a test ended
with `assert stale in stale`, which is true of every string and tested nothing.

### `/setup-boards` was answered by improvisation (`services/api/setup_routes.py`, `libs/agents`)

Found in the live run. A typed slash command was never a command: it reached the model as ordinary
text in whatever conversation was open. `/setup-boards` does not exist on this platform at all --
`SETUP_AGENTS` is context, setup, role-angles, scorer, plus the director and prompts steps -- so the
agent did the reasonable thing with an instruction it could not carry out and invented a
destination, announcing it would write the search configuration into two profile files instead.

Nothing errored. Nothing was refused. The user was told a plausible sentence about work that could
not happen, and the "configuration" went somewhere nothing reads as configuration: what actually
drives the harvest is `user_config`, written from the Settings page, and no agent tool touches it.

Now refused before the model sees it, in three cases: the conversation's own command passes (that is
how a chat starts), a real command names its own chat, and an absent one says so plainly. The prompt
side is told which commands exist here and that search settings are not its to write -- both needed,
since the ported bodies still name `/setup-boards` and friends and a user reading one will type it.


### Agent prompts: two guardrails on what the user is told (`libs/agents/__init__.py`)

**Prompt change, made on the owner's explicit instruction** — the standing rule is that prompts are
not edited without one. Both rules were added to `ENVIRONMENT`, the block appended to every command,
rather than to any individual prompt: the ported bodies stay byte-identical to the reference
instance, and a command added later cannot be written without inheriting these.

**Internals are never named to the user.** A real setup transcript ended with a summary table of
`context.md`, `base_cv.html` and `08-role-angles.md`, and told the user to run
`powershell -File cv/compile.ps1` and check whether Lato rendered instead of Arial. None of that is
theirs: the filenames are the platform's names for its own storage, and the script does not exist on
their machine. It reads as a demand to manage machinery they cannot see and are not responsible for.
The prompt now requires plain terms — "your CV is written and ready" — and points at the file list
on their own page, which is already labelled in words, when something must be pointed at.

**A command that cannot run here is a new chat, said in those words.** One conversation runs one
command and cannot hand off inside itself. The transcript said "start it as a new command", which a
user reasonably read as "type it here" — and typing it there does nothing at all. The required shape
is now literal: *"Open a new chat and run /setup-boards there."*

`apply.md` and `setup.md` still contain the `cv/compile.ps1` lines they were ported with. They are
left alone deliberately — divergence from the reference instance is the thing the porting rule
exists to prevent — and `ENVIRONMENT` already overrides them, both by saying the model cannot run
commands and now by forbidding it to pass one on.

Tests assert both rules are present for every startable command, so dropping them fails the build.

---

## 2026-07-31

### The password floor drops to nine

12 was turning invited beta users away at the door, which costs more than the marginal entropy
buys (owner's decision). `MIN_PASSWORD_LENGTH = 9` — signup, reset and change all derive from it,
and the three frontend hints were the only places the number was written out by hand.

The `_OBVIOUS` list was written against a 12-character floor, so every entry in it was already
longer than the minimum and the nine-character common passwords could never have been reached.
Nine of them added.

`AUTH_REUSE.md` §4 and `ROADMAP.md` 1a said 12; both now say what the number is and why it moved,
rather than being left to contradict the code. A test pins the literal 9 and the 8/9 boundary —
every other password test reads the constant, so a future change to it would otherwise pass
silently.

### The embedding rebuild, and four things that stopped it finishing

Backfilled 2026-07-31: these nine commits shipped without changelog entries. The reasoning is in
the commit bodies and is summarised here because that is where it is looked for.

**Why there is a rebuild at all** (`0bfc773`). The platform embedded `description` alone; the
reference's default is `title_desc` — title first, then description, capped at 4,000 characters.
Two consequences, and the second is the one that mattered: the title is the single strongest
statement of what a job is, **and it has to lead, because the encoder truncates**. `bge-base`
reads ~512 tokens; Arbeitnow descriptions average 6,662 characters, so a title appended at the end
would be cut off every time. Those sources scored 16–22 against LinkedIn's 43. The mode is folded
into `EmbedderVersion.version`, so every existing vector went visibly stale rather than
confidently wrong — the honest price being a re-embed and a retrain. Also fixed: locations reached
the boards as slugs, so LinkedIn was sent `european_union` and one query died with
`Invalid country string`.

**Then the rebuild would not run.** Four failures, each found only by watching the live box:

- `0a80ffc` — eight consecutive cycles rebuilt zero vectors. Each holds the shared advisory lock
  for its whole run, so every cycle after the first was locked out by the one before and the loop
  looked like it was working. `--embed-only` takes no lock and does nothing but embed.
- `d039f52` — that pass ran the whole 6,601-row rebuild inside one transaction, committing
  nothing for an hour. A crash at minute fifty-nine discarded fifty-nine minutes of paid
  embedding, and while it ran the database still read zero embedded, so a working run and a wedged
  one were indistinguishable. Now a session per 200-row batch.
- `3265519` — the rebuild then died at sixty minutes on a Postgres deadlock against the scheduled
  cycle's own writes to `corpus`, and rolled back everything. Ordering cannot fix contention
  between two different write patterns, so a failed batch no longer ends the run: the rows stay
  pending for the next pass, capping the loss at 200 rows. Five consecutive failures still stops
  it, so a broken embedder does not spin.
- `bd305ad` — the first batch of the rebuilt run died on a raw `httpcore.ReadTimeout`. `_post`
  wrapped HTTP status errors but let transport errors through untouched, so every caller — all of
  which handle `EmbedderError` — was killed by a timeout anyway. Wrapped, with a test. The timeout
  itself was contention with the scheduled cycle on a single-threaded encoder, so `--embed-only`
  now uses a 900s timeout and a batch of 64.

**Position at the time of writing: 3,136 of 6,601 rows re-embedded, 3,465 pending, one version.**
Both users' active models were trained on the old vectors and cannot be retrained until this
finishes.

### The ingestion gap closed: LinkedIn runs, and the source list was wrong

`23f461d`, `0610a40`. The measured cause of "my feed is quiet", and it was neither the scorer nor
the filters — both were suspected first and both were fine. LinkedIn produced 337 of the founder
account's 348 jobs at or above the cutoff; the sources running in its place produced **nine in two
days**.

**The per-user LinkedIn pass did not exist.** Every part it needs was already built — adapter,
lease, governor with its concurrency ceiling and interval floor and kill switch, both scheduling
columns on `users` — and the loop between them was missing, which is why every LinkedIn row in the
corpus carried the same `first_seen_at`: one import, and nothing had fetched since. It is separate
from the shared cycle because a different person pays: the cadence, the bandwidth and the ban risk
are the user's, so one account's quarantine cannot stop everyone's feed.

**And the enabled source list was the wrong seven** — all remote-work aggregators, which the
reference instance does not use at all. They were a deliberate first notch and the ramp then
stopped, because nothing measured that it had. Now the reference's own set: ATS boards where
employers post directly, VC boards, and the European boards where this platform's users are.

`jobspy` was never added to the harvester requirements despite three sources depending on it, so
those sources could not have run once — and nothing surfaced it, because they were also not
enabled. Two halves of one omission, each hiding the other.

**Confirmed working since:** 24 hours of intake now shows linkedin 212, hiringcafe 167,
nofluffjobs 87, profession 53 alongside the aggregators — sources that produced nothing before.

### The scorer stopped asking a regression for a classifier's number

`eebeaee`, `b3e65a6`. The live reference model settles it: `model_type=histgbm`, `task=classify`,
`decision_cutoff=44`, against a `score_threshold` of 60. The platform used a fixed
`Ridge(alpha=1.0)` regression and took **60** — which is the *label* threshold deciding what counts
as fit while training — as its delivery cutoff. A regression fitted on mostly-low labels pulls
toward the mean, and the platform then demanded 60 of it while the reference asks 44 of a
calibrated classifier. That is why the feed went quiet even where jobs existed.

Now four candidate heads, class-balanced with calibrated probabilities, selected on
cross-validated average precision — not ROC-AUC, which is optimistic at a quarter positives, and
not the held-out split, which would be selection-on-test. The winner is refit on everything before
deploying.

Separately, `b3e65a6`: fifty candidates, fifty dropped for age, zero sent, while twenty-five fresh
matches sat one page further down. The pool is ordered by score and the age filter ran only after
selection, so a backlog of month-old high scorers took every slot. The filter runs in the query
now; undated rows still come through, because they are what the pre-send dating exists to resolve.

### The setup flow can now be finished, and `setup-all` is retired

`setup-context` and `setup-director` are live. With them, **the four steps produce exactly the eight
files the trainer gates on** — a property a test asserts rather than a claim: every step is checked
for owning at least one required file, and the union is compared against `setup_files()`, so a stray
step or a missing file fails the build instead of stranding an account.

`context.md` has an owner for the first time. Until today nothing wrote it while
`setup-role-angles` required it, which made setup unfinishable and — after the training gate landed
— training unreachable for every new user. `UNOWNED_FILES` is now empty and a test keeps it that
way: a required file with nobody responsible for it is an account that cannot finish.

**`setup-all.md` is deleted.** It was unreachable, superseded, and its gates verified a filesystem
this platform does not have; leaving a broken director in the tree is how one gets wired up by
accident. What it was measured against is recorded here rather than in a parallel file:

| | `setup-all` (retired) | live |
|---|---|---|
| References to things this platform lacks | 11 | 0 |
| Required files never mentioned | 6 of 8 | 0 |

The order is now stated as a constraint, not a preference — `setup-context` first, because
`setup-role-angles` says outright that `context.md` must already exist, and running it earlier
produces files grounded on nothing.

### Applications are training data now, and a draft director that can actually run here

**98 real applications became labels.** They had been sitting as `applied_at` timestamps since the
import while the trainer ran on 3,498 Haiku guesses — the strongest signal the platform will ever
get about what this person wants, unused. Only 7 of the 98 had ever been labelled at all, and the
LLM had *rejected* 4 of those: jobs he cared enough about to apply to, scored below the keep
threshold. Written at weight 5 against the LLM's weight 1, so 490 weighted true positives now
outrank the guesswork. A script rather than a migration, because it is a judgement someone should be
able to run for one account and inspect.

### The setup path could not complete, and nothing said so

Walking it before testing with a real user, which is the whole reason to walk it. **Three of the
eight required profile files belonged to steps nothing could start.** The chat sent no command name,
so every turn ran `setup` — which writes five. `context.md`, `08-role-angles.md` and `label.md`
belong to `setup-role-angles` and `setup-scorer`; the API accepted both and the UI never offered
them. Combined with the training gate added the same day, **a new account could not finish setup and
could therefore never train** — and the two failures would have looked unrelated.

Fixed: a leading `/command` selects the step, the empty chat opens with a **Start** button rather
than a blank box, and `GET /setup/progress` answers what is missing and which command produces it.
**The success criterion lives in code**, and it is the same check `train_user` refuses on, so the
page and the trainer cannot disagree about whether an account is ready.

### A draft director, measured rather than asserted

`setup-all.md` is a verbatim port of the reference instance's local director. Its gates verify
`pipeline/config.yaml`, `scorer/models/scorer.joblib` and `.env` — **none of which exist here.** An
agent told to "verify mechanically" against paths that are not there either invents a pass or never
stops, which is exactly the runaway the owner asked to design out.

`setup-director.md` is the platform rewrite, shipped **as a draft**: it is not in `SETUP_AGENTS`, so
nothing can start it until it has been reviewed. Two properties are checkable without spending a
credential, and the test asserts both — including that the *current* file fails them, so if somebody
fixes it in place the test says the draft is redundant:

| | `setup-all` | `setup-director` |
|---|---|---|
| References to things this platform does not have | **11** | **0** |
| Required files never mentioned | **6 of 8** | **0** |

It also carries what the old one had no notion of: an explicit **stop** condition (a finished profile
is finished — no fourth pass, because every extra pass spends the user's own credential and looks
identical to progress), and a pass that asks for the experience a CV leaves out — internships, side
projects, volunteering — which goes into `context.md` rather than onto a CV.

Writing it caught one defect in itself: the draft's header originally explained what the old one got
wrong, naming those dead paths *inside the prompt the model reads*. That rationale lives here and in
the test instead.

### The chat reads like a conversation

The assistant now answers inside a container on the left, the user's bubble stays on the right, and
what a turn *did* is a rail of dots — one step per file — rather than a single line running several
writes together with separators. Before this a long reply and the page around it were hard to tell
apart, and a turn that wrote three files had nowhere to show it.

### A model may no longer be built from a half-written profile

**The failure this prevents is silent, which is why it needed a gate rather than care.** A model
fitted while setup is unfinished is fitted against a rubric answering a different question from the
one the finished profile would ask — and nothing downstream reveals it. The labels look like labels,
the metrics look reasonable, and the feed is quietly wrong for as long as the artifact lives.

It was already live: one account holds four of its eight setup files and is missing `base_cv.html`,
`label.md`, `04-job-evaluation.md` and `08-role-angles.md` — and has 945 labels and an active model
trained from them.

`train_user` now refuses unless every file the setup agent owns is present and filled. That set is
the seeded scaffolds minus the ones that ship usable, so the task prompts and the CV framework —
which are already good and only want tuning — gate nothing. The check lives in `train_user` rather
than the queue handler, so every caller is behind it: the worker, a script, and the manual retrain
button that does not exist yet. Six existing tests were training without a profile and now say so.

**And the flag that describes all this had drifted.** `filled` is derived data living in a column,
so it can disagree with the content it describes, and on a live account it did: four files of real,
marker-free text — 13k to 32k characters each — every one flagged empty. `grounding()` and
`missing_setup()` read the content and are immune, but `listing()` reports the column — so the
*setup agent* was told its own finished files were still scaffolds, and might set about writing them
again. `repair_filled_flags()` recomputes from content, which is the authority; both live accounts
have been repaired.

### The status page slept through a six-hour outage, so it learned to watch delivery

Every component was green while nothing was being delivered: collection running, scoring running,
bot and channel connected, and the notifier completing its jobs having sent nothing for six hours.
**That is the precise failure a status page exists to prevent**, and this one could not see it,
because it checked whether each part was alive and never whether anything came out the end.

There is now a `delivery` component, and what it reports is a *conjunction*: matches waiting **and**
nothing sent. Either alone is meaningless — an account with nothing above its cutoff is quiet for
good reasons, and a backlog straight after a harvest is normal — so only together do they mean
stalled. Warn at 3 hours, down at 6, wider than the harvest thresholds because delivery is bounded
by the user's own notify interval and daily cap. The test pins the exact shape of the outage.

### Notifications can be dismissed, and Telegram's channel is no longer optional

Per-item dismiss, mark-all-read and delete-all, the last two as an eye and a bin — two text buttons
side by side were wider than the panel they sat in. Deletion is a real `DELETE`, not a hidden flag:
these are transient operational messages, and once acted on there is no later question a kept row
answers. The row markup had to split, because the dismiss control cannot be a button inside a button.

**The Telegram channel is now required, in onboarding as well as in settings.** It was optional, and
that was wrong: delivery is the product, and the private bot chat is for commands and finished
documents — a feed nobody scrolls is not a feed. An account could previously finish onboarding with
a working pipeline and nowhere to put its output, which surfaces months later as "it stopped sending
me jobs", the least diagnosable shape a problem takes.

The chat ID and channel ID are now displayed. **They are addresses, not secrets** — useless without
the bot token, which is never returned — and withholding them only stopped a user confirming that
the thing they configured is the thing we send to, which is the one question that page answers.

### Verified rather than assumed

Everything above ran against a real Postgres, not the 472 tests that skip without one: **1,034
passed, 11 skipped.** That is also the answer to whether the auth flows work — signup, login,
change password and email verification are covered across `test_api_auth.py`, `test_auth.py` and
`test_gdpr.py`, including the ones that matter most (an unverified account cannot log in, a
verification token is not a reset token, a reset ends every session, a changed password requires the
current one).

Delivery pause was traced end to end rather than eyeballed: the toggle writes `user.active`, and
`dispatch_due_notifications` filters on it — so pausing stops jobs entering the lane at all, instead
of enqueuing work that is dropped later.

**One bug this caught in my own change:** requiring the channel broke `test_onboarding.py`'s fixture,
which connected a bot without a route. It passed locally because those tests are DB-gated and skip
without Postgres — it would have failed in CI. Fixed, and pinned with a test.

### Four bugs between a re-embed and a trained model, each hiding the next

The corpus had to be rebuilt because the platform embedded `description` alone where the reference
embeds `title + description`, so every stored vector was in the wrong space. Getting 6,601 rows
through that rebuild took four fixes, and their shape is the lesson: **each one only became visible
once the one in front of it was gone.**

**1. One transaction for the whole rebuild.** `--embed-only` called `embed_all_pending` inside a
single `session_scope`, and that function drains up to fifty batches before returning. Twenty-five
successful `/embed/bulk` calls had already happened and `corpus.embedding IS NOT NULL` still counted
zero, because nothing commits until the end. Now one batch per transaction: the run is
interruptible, its progress is readable from the database, and a stop costs at most one batch —
which is what "we can start and stop this process" actually requires.

**2. A deadlock destroyed the first hour.** The shared harvest cycle writes the same table — ingest,
description refresh, cold trim — and Postgres resolved the lock cycle by killing the re-embed. Sixty
minutes of paid embedding rolled back. `awaiting_embedding` already orders by id, so embed-against-
embed was never the problem and ordering one side cannot fix it. Instead a failed batch no longer
ends the run: the rows stay pending and are picked up again.

**3. A raw timeout killed the next attempt on its first batch.** `EmbedderClient._post` wrapped HTTP
status errors and short responses in `EmbedderError` but let transport errors through untouched, so a
read timeout escaped as a raw `httpx` exception past every caller — all of which handle
`EmbedderError`. Now wrapped, with a test.

**4. And that timeout was contention, not a broken gateway.** The cycle embeds too, and two clients
queued on a single-threaded encoder push a 200-text call past the 120-second client default. A bulk
embed is not a web request: the rebuild now uses a 900-second timeout and batches of 64, so each call
finishes in ~33 seconds even under load, with a shorter row-lock window per commit. **Worth noting
this was fixed rather than worked around** — `sudo systemctl stop harvester.timer` was unavailable,
which forced the better outcome: a rebuild that survives alongside a live pipeline rather than one
that requires the pipeline to be stopped.

Result: 6,601/6,601, every row carrying `…|norm=1|text=title_desc`.

### A model is stale when its coordinates move, not only when labels arrive

Both retrains then went dead with *"not enough new labels since the active artifact"* — while the
active artifacts were already unusable and the scorer was failing closed on the version mismatch.

`should_retrain` compared label counts only, which assumes the one thing that can go stale is the
evidence. It can also be **the coordinates**. Re-embedding moves every vector, and a model fitted in
the old space cannot read the new one: its weights are indexed to axes that no longer mean what they
meant. None of that arrives as a new label. The guard now reads `embedding_version` off the corpus
rows the fit would actually train on — the vectors are the ground truth about which space they are
in — and retrains when it differs from the active artifact's recorded `embedder_version`.

First result, on Andras's 945 labels: **histgbm selected**, cross-validated average precision 0.622
against svc 0.610, logreg 0.468 and linsvc 0.467; test AP 0.545, AUC 0.79.

Then the owner's, on 2,554 labels: **svc selected**, CV-AP 0.663 (histgbm 0.660 a close second,
linsvc 0.532, logreg 0.518), test AP 0.665, AUC 0.832 — and an operating point of **cutoff 38 at
65% recall**, precision 0.616. Near the reference instance's 44, and nothing like the 60 that was
emptying the feed. All 6,978 rows rescored against it; 1,251 clear the cutoff.

### An unreachable recall target must not silently become the label threshold

A flaw in the bake-off added earlier the same day. When no threshold in 20–90 held the target recall
out of fold, the sweep fell back to `FIT_THRESHOLD` — **the label threshold, reproducing the exact
conflation that had just been fixed**. It hit Andras's model: cutoff 60 at 37% recall, with nothing
to say the target had been missed. An unreachable target now takes the best-F1 point, and `metrics`
records `recall_target_met`, which is the difference between a feed that is thin by design and one
that is thin for reasons nobody can name.

### Fifty candidates, fifty dropped, nothing sent

With the model fixed and 26 genuinely fresh matches waiting, the notifier selected 50 candidates and
dropped every one — `{'too_old': 28, 'closed': 22}` — twice in a row.

The age filter in SQL has to let undated rows through: LinkedIn reports no posted time to the
listing API, and `date_candidates` exists to resolve them. But the pool was ordered by score alone,
and undated rows are the bulk of the corpus and indistinguishable from fresh until something fetches
them. They took every slot, only a handful could be dated per run, and the rest resolved to old —
while the jobs we *could* prove were recent scored lower and never entered the window.

Every dated row in the pool has already passed the freshness window in SQL, so preferring them costs
nothing. Undated rows still fill the remaining slots and are still dated: ranked behind certainty,
not excluded by it.

**Verified end to end**, which is the point of all of the above: *Backend Engineer (Python/AWS)* at
Digital Waffle, score 90, posted 12:47 UTC and delivered to Telegram at 13:47 — the first delivery
since 09:11, and the first ever through the corrected vector space.

### The schema CV renderer, deleted, and five functions nothing called

A scan of every module-level function against every mention of its name in the tree, plus an import
graph over the 233 modules and 56 test files. Two categories came out of it, and only one of them
was safe to act on — the distinction is the useful part of this entry.

**Genuinely dead: the CV renderer.** `libs/templates/cv.py` filled the base layout from a
`ProfileSchema`, and `services/docgen/build.py::base_document()` was its only caller — which had
been labelled *Deprecated* in its own docstring since the base CV became a per-user file. Nothing in
the platform reached either. A real build reads the user's `base_cv.html` out of `user_profile_files`
— the document the setup agent wrote, with their actual experience in it, where the renderer could
only emit what a schema had fields for. `run.py` already raises `NotBuildable` when that file is
missing, so the renderer was not even a fallback.

607 lines gone (renderer plus its tests). **What was kept is the point:** the layout itself is still
live — docgen hands it to the tailoring prompt as the BASE TEMPLATE — so the five tests that cover
*the file on disk* moved to `tests/test_cv_template.py`, including the `@page` margin check that
caught a real bug (body padding gives page 1 a top margin and page 2 none, which is what a recruiter
sees past the fold). The seven `test_docgen.py` tests that called the renderer never wanted a
rendered CV; they wanted *a* base document, and now load the template directly.

**Five functions nothing named, removed** with the imports they orphaned:
`features.feature_matrix`, `build.prompt_text` (its docstring said "exposed so a test can assert
this" — no test ever did), `deliver.render_digest`, `eligibility.worth_applying_now`,
`proxy_usage.next_local_midnight`. `render_digest` is the interesting one: it documented itself as
superseded, because a Telegram keyboard attaches to *a message* and so a digest structurally cannot
carry per-job buttons. That reasoning is worth keeping — which is what this entry is for, and why
the code did not need to stay to hold it.

**Deliberately not deleted, and why.** `libs/corpus/postings.py::fetch_apply_url` is unused but sits
in a file `pyproject.toml` pins as kept diffable against the reference instance; deleting parts of a
ported file destroys the one property that makes it reviewable. And the whole **document-capture
flow** — `services/capture`, the `process` lane, `/process`, `ProcessPage.tsx`, `libs/scoring/facts`
and the `users.facts_md` it writes — is *superseded but wired end-to-end and passing its tests*.
That is not dead code, it is the previous product surface still reachable from the dashboard, and
retiring it is the `REBUILD_PLAN.md` phase-5 cutover rather than a cleanup. Noted here so the next
scan does not have to re-derive it. One genuine orphan found and left alone: `users.profile_context`
is declared on the model and never read or written by anything, but dropping a column is a migration
and the corpus is mid-re-embed.

567 tests pass, ruff clean.

## 2026-07-30

### A chat turn that survives the browser, and the cap that was never enforced

**The turn ran inside the HTTP request, and that was the bug.** It takes minutes on a frontier
model, so switching tabs, opening another chat, refreshing or signing out aborted it — the user's
question was already committed and the reply never arrived, with nothing on screen to say why.
Persistence was never the problem; the request's lifetime was.

So a turn is a queue job now (`setup` lane, `services/setupagent`). The API writes the question and
a **pending** assistant row and returns `202` immediately; the worker streams text into that row as
the model produces it and marks it done. The page polls while anything is unfinished, so what it
renders is the same row whether somebody watched it arrive or came back the next morning — with a
caret while text is landing, and a failed turn written down rather than swallowed, because a chat
that silently stops answering is indistinguishable from one still thinking.

Proven in a browser, not asserted: send → thinking state → streaming text → **navigate to Jobs
mid-turn** → come back → the reply is there and complete → full page reload → still there.

**Two bugs found on the way.** `CLAIM_TIMEOUT_MIN` had no `setup` entry, so the new worker
crash-looped on its first sweep rather than its first job — a test now pins that the lane list and
the timeout map agree by construction. And a stored config section written before `locations`
existed has the key absent, which the old `<select multiple>` tolerated and `.includes()` does
not: the whole details page went blank. Defaults are spread under the stored section now.

### Telegram: your own cap was being ignored

Nothing was arriving, and it was not the proxy. Two separate causes, both now understood:

- **`max_notifications_per_day` was read by nothing.** It is in the config schema and on the
  dashboard ("At most 20 messages a day"), and `deliver()` used its own hardcoded 100 regardless.
  The account that asked for 20 received **160 in sixteen hours**, which is what exhausted the
  window it is now stuck behind. `deliver` reads the user's config (migrated, so v1 rows count
  too), with tests for both shapes.
- **The other account's candidates are all too old** — 44 unsent jobs at or above the cutoff, none
  posted within its 6-hour window. That is the JobSpy/LinkedIn ingestion gap, not a filter fault.

The cap counts from the user's local midnight, so the account that overshot resumes on its own.

### Everything else asked for

**Deleting a credential asks first**, naming what stops: job delivery, the profile chat, LinkedIn
collection. A one-click destructive control next to a copy button is a mis-click waiting to happen.

**Freshness lives in Settings alone.** It was on both the config form and the delivery card, and
nobody could say which was in force — they wrote the same field, so both worked, which is worse
than one being broken. Settings keeps it, beside the interval it is so often confused with.

**The config form was rebuilt for people who do not read forms.** Two newline-separated textareas
and a `<select multiple>` became chips you add one at a time and labelled checkboxes grouped into
"work from anywhere", "regions and countries" and "cities" — with the distinction between "remote
from the EU" and "in the EU" written out, because it is the one everybody gets wrong. Onboarding
and Your details share the component, so both changed at once.

### Three chats per account, each with its own memory

Persistence was never the gap — the transcript has always been a table, so a closed tab, a
sign-out and a week away all resumed the same way. What was missing was **more than one of them**.

`setup_conversations` is the new unit of context. Messages belong to one, `history()` filters on
it, and the model is only ever shown the chat being spoken in. A new chat starts empty; the old
one keeps everything. What crosses between them is the **profile**, not the talking — the files
are the durable product and every chat reads and writes the same ones, which is what makes
starting fresh cheap rather than a loss.

**Three, fixed, not a setting** (owner's call). Every turn replays its whole transcript on the
user's own credential, so unbounded chats is unbounded spend on re-reading them. Deleting one is
how you make room, and the refusal at the cap says so rather than just saying no. Deleting a chat
never touches the profile, and deleting the last one is allowed — the next visit opens a fresh
one, which is where a new account starts anyway.

Titles come from the first thing the user says, trimmed. No naming call: a title is not worth a
request that can fail, and the first sentence is what somebody would have typed anyway.

Existing transcripts migrate into one conversation per user rather than being discarded — whatever
was said during setup is the reason the profile looks the way it does.

**The browser run earned its keep again.** A fresh chat was titled "New chat" and the button that
creates one said "+ New chat" — two identical labels side by side in the switcher, and the first
automated click hit the wrong one. Untitled chats are "Untitled chat" now. Eight new tests cover
persistence across sign-out, context isolation asserted at the enforcement point, the cap, delete,
and that another account's conversation id is useless.

### The evening batch: twelve owner asks, hardest first

**In-app notifications** — `user_notifications`, `libs/notify` with event templates and
collapse-onto-unread (producers run on timers; forty copies of "Telegram is broken" is how a bell
gets ignored), a list/mark-read API, and a polling bell in the nav. Producers wired where the
facts are learned: bot revoked, model trained, processing drafted and failed, password changed.

**A status readout on the Overview** — `GET /status` derives each component's health from the
data (bot, channel, harvester freshness, scorer, docgen), each a colour and a sentence, because a
red dot with no words is a support request.

**The chat became a chat and the Model page exists.** User in a bubble, assistant as prose with
markdown-lite rendering, autogrowing Enter-to-send composer, an empty state that suggests what to
say. Training moved to its own nav page — the chat edits what the platform knows; the Model page
owns label → train → rank, with the one paid action as a button and the free retrain described as
what it is.

**Jobs can stream to a channel.** `PUT /credentials/telegram/channel` proves the ID by posting to
it (a bot must be an admin to post — the test send is the whole check, and its failure strings are
the user's fix), then routes every stream there; the bot chat keeps commands and documents.
@getids walk-through folds sit beside every fiddly Telegram step. No message ID is stored — the
channel ID alone is what sending needs.

**Credentials show what is stored** — masked last-4, replace, delete, and copy behind the account
password. `POST /credentials/{kind}/reveal` is the one exception to write-only, and the shape of
the exception is the point: plaintext is what "usable" means, so the security is the gate — the
password (a stolen cookie is not enough), one response, an audit line either way, a rate limit.

**Documents are uploads only.** The history is one document by construction now (a new upload
replaces it, behind an are-you-sure), it no longer appears in the Documents list, and
`scripts/move_md_documents.py` moves the migrated profile `.md` files out of both live accounts'
Documents into the profile store where the chat maintains them. Agent coverage audit: `setup.md`
writes every per-user file; `03-writing-style` and `06-cover-letter-templates` are platform-shared
by design (`libs/profiles` EXCLUDED) and are not supposed to be per-user.

**Delivery has an on/off switch** (`PATCH /account {active}`) with the honest sentence beside it:
messages stop, collection and scoring keep running, the bot stays on.

**The landing page stopped being an open-source pitch.** GitHub links and self-hosted framing out;
same length, same sections, same wheel — Get Started is now "set it up once / the radar takes
over" ending in Create your account.

**Small but asked for:** the jobs table shows when each job was fetched ("30 Jul 20:04 · 3d old")
sortable on that column; the split-second "Loading…" flash on Jobs, Profile and Settings is gone
(module caches render the last answer while the fresh one loads, cleared on sign-out so the next
account cannot flash this one's data); and the dev proxy learned `/notifications` and `/status` —
the same class of gap `/setup` fell into, this time caught by the browser pass before it shipped.

### Deployed, and the two things deploying found

`api` and `notifier` rebuilt and restarted on the box, running everything above.

**The api container was never given `PUBLIC_URL`.** It was in `.env.example` and in nothing that
reads it, so `_base_url` fell back to `http://localhost:8000` and every verification and reset
mail production ever sent pointed the recipient at their own machine. Set on the box, added to
`compose.yml` as `:?`-required — a silently wrong link is worse than a container that will not
start — and asserted in `tests/test_mail_config.py`, beside the sender-selection test written
after the same shape of outage. Verified live: the API now builds
`https://mahoraga-app.rubenlekli.com/#/verify?token=…` and resolves `SmtpSender`, not the console.

**The notifier crash-looped on `ModuleNotFoundError: pydantic`.** The v1-config fix imported
`libs.contracts.config` into a service that never declared it — the indirect-dependency gap the
`service-deps` CI job exists for, reproduced locally by installing the notifier's requirements
alone into a clean venv and importing its entrypoint. Declared, rebuilt, both containers stable.

### The e2e browser run, and the three bugs the API tests could not see

"I can't log in and reset doesn't work" — reproduced by driving the real dev stack (vite, uvicorn,
Chromium) through signup → emailed link → verify → login, then forgot → reset → old password
refused → new one works. That run now passes end to end. What it caught, none of which any API
test could have:

- **`/auth/verify` took the token as a query parameter; the page sends a JSON body.** Every human
  verification failed 422 while the tests stayed green — they used the query form. The endpoint
  takes a body now, matching what the SPA sends.
- **StrictMode consumed the verification token twice.** The second POST finds it used, and the
  page flips from "confirmed" to "did not work" *after succeeding* — both requests are valid
  requests, and only a page that makes both misbehaves. A ref guards the effect.
- **A stale signed-in state survived the reset.** Hash navigation never re-checks the session, so
  an app that was open before the reset link kept rendering its shell after every session was
  revoked. The reset page tells the app to re-ask.

And the reason the flow was unfinishable locally at all: **`ConsoleSender` kept the link in memory
and logged only the subject** — and could not have logged the body, because the auth routes send
inside `redacting(token)`, which scrubs the token from every log record. It prints to stdout now,
which the redaction filter does not touch, with a test pinning that the link is visible.

### Onboarding wears the design, and everything collected becomes editable

**The wizard's progress is numbered circles now** — done is filled with a check, current is
ringed, the future is a dim number, labels underneath, joined by a rule. Finished circles are
clickable, which is what saving per page always bought and the thin bars never said. The mechanics
did not move: which page you are on is still the server's answer, and nothing here can skip.

**"Your details" is a page** (`web/src/pages/DataPage.tsx`): every onboarding form, rendered by
the same components the wizard uses, in closed folds — name and links, documents, history, search
config, and the three credentials. Not copies: a field added to onboarding appears here without
anyone remembering to mirror it, and a correction saves exactly the way getting it right the first
time would have.

**Settings grew the two delivery knobs**, worded for the confusion between them: how often we
message you (`PATCH /account`, new, bounds mirroring the DB constraint) and how fresh a job has to
be (the config's `posted_within_hours`, down to one hour now in both forms). The interval is when
we check; the freshness is what is still worth sending when we do.

### The live box check, and what it found

Prompted by "make sure my account gets last-hour jobs". Ran against the box read-only, then fixed
what it turned up:

- **The delivery age filter works** — since the 11:15 UTC image rebuild, every cycle logs
  `dropped {'too_old': N}` and sends nothing stale. The 160 stale sends earlier that morning
  predate the rebuild, and they burned the owner's daily cap, which is why his feed then went
  quiet: the cap frees itself as the 24-hour window rolls.
- **A v1 config's own answers were being ignored.** `Eligibility.from_config` reads sections; a
  v1 row keeps `posted_within_hours` and `exclude_keywords` at the top level — so the account that
  asked for 72 hours and typed exclusions was getting the 24-hour default and none of them.
  `from_config` migrates first now, inside the method so no future caller can repeat it.
- **Fresh high scorers are scarce because LinkedIn ingestion does not exist** — the handover's
  known largest gap, confirmed in numbers: 154 fresh jobs in six hours, all scored, none ≥ 60,
  because the enabled no-auth REST sources top out at 57 for this profile and every historical
  ≥ 60 came from the one-time LinkedIn import. The fix is the JobSpy tier, not a filter.
- The Telegram card's age element (`· <1h`) was already the reference's, ported line for line —
  verified rather than changed.

### The auth audit: two findings, both the missing half of something shipped

Prompted by the owner asking whether everything ported from the FlowSolutions reference
(`AUTH_REUSE.md`) actually works. The §3 decision table checks out line by line — argon2id at the
production parameters, tokens hashed at rest, 15 min/30 day TTLs, cookie flags and the scoped
refresh path, the 12-character minimum, no v1 path. What the audit found was not a wrong decision
but two flows whose libs half existed and whose route half did not:

**The emailed verification link pointed at a POST-only API path.** `{PUBLIC_URL}/auth/verify?...`
— a mail client GETs, and the answer is a 405 JSON error. Both accounts were migrated rather than
signed up, so no human had ever clicked a real link. Both emails now point at the SPA's hash
routes (`/#/verify`, `/#/reset`), and a test pins that.

**Password reset did not exist.** `reset_password` tokens, their one-hour TTL, and the mail
template all shipped in Phase 3 with nothing calling them — a forgotten password was a locked
account, on a platform whose accounts hold paid credentials. `POST /auth/forgot` (opaque always,
same no-oracle rule as login; a fresh link revokes the previous one) and `POST /auth/reset`
(strength-checked, revokes every session, does not log in — proving control of an inbox is not a
login, and a verification token is not a reset token) close it, with `#/forgot` and `#/reset`
pages behind the sign-in form's "Forgot your password?".

**Password change from settings**, same commit family: `PUT /account/password` with the
reference's semantics — current password required, new must differ, every session dies including
the one asking, cookies cleared in the response rather than left to expire.

**And CI scans for leaked secrets.** Gitleaks over the full history on every push, because a key
committed and then removed is still in every clone. The first full-history scan found exactly one
thing: a test's literal `"argon2-placeholder"`, now pinned by fingerprint in `.gitleaksignore`
so the suppression cannot widen. The local-only credential files were confirmed gitignored.

### The app looks like the landing page, and the jobs page becomes a table

The two top items of the handover's work order, plus the setup-chat fixes beneath them.

**The design system is the landing page's** (`mahoraga-jobs-web/src/index.css`), carried into
`web/src/styles.css` wholesale: `#060606`, hairline borders at falling ink opacity, mono uppercase
kickers, pill buttons that invert on hover, the faint dot-grid backdrop, 0.25 s transitions and a
single fade-up on route change. **Dark is the default now, not the system preference** — the
landing is dark-only, and the app opening in white read as a different product. The toggle stays
and offers light, an equivalent inverse built from the same variables, so the palette remains one
set of rules. Every existing class name survived the rewrite; no page needed restyling by hand.

**`GET /feed` grew the parameters a real table needs**: `offset` and `total` for pagination, `q`
over titles, companies and URLs, `source`/`remote`/`applied`/`max_age_days` filters, and
`sort`+`direction` over score, posted date, title and company. The age filter keeps undated rows —
the same fail-open rule delivery uses, because a missing `posted_at` is unknown, not old. The
salary column is extracted server-side per page by `libs/corpus/salary.extract` (regex, free) so
the description still never crosses the wire, and `applied_at` travels with the row. The stage-0
feed takes the same filters in Python, after its scoring loop, so the two stages cannot behave
differently. Seven tests, including one asserting the page and its `total` are computed under the
same filters — a pager that says "1 of 3" against a differently-filtered count lies.

**The jobs page renders that**: sortable column headers, filter chips, a debounced search box with
a response sequence guard (a late answer to an old query must not overwrite the current one), and
prev/next paging. The filters live server-side deliberately — filtering the loaded page in the
client silently lies whenever the answer is on a page that was never fetched.

**The setup chat's file list works now.** The entries linked `#/setup`, a route that does not
exist, so clicking one navigated to the overview instead of opening the file — the "clicking does
nothing" from the handover. They are buttons now, in a card grid with a monochrome icon per file
kind and short human names ("How you work", not `02-behavioral-profile.md`). The pending line has
typing dots beside the honest sentence about minutes — the dots say working, the words say how
long, and neither alone is enough.

972 passed against a real Postgres; `openapi.json` and `api-types.ts` regenerated.

### The instruments, and the nine bugs they found

Most of today was building things that measure, then believing them when they disagreed with me.
The measurements are described last because the bugs they surfaced matter more.

**A subscription OAuth token needs the Claude Code identity block, or it 429s.** The entitlement is
checked against the *first system block*; anything else is refused with `rate_limit_error` — an
error that reads as "out of quota", is politely retried by every client, and never clears because
nothing was consumed. Measured live: identical call, 429 with a plain system prompt, 200 with the
block in front. Haiku passes either way, which is why it hid — labelling and CV builds both run on
Haiku and worked, while the Opus setup agent would have failed on **every OAuth account**, telling
users they were out of quota.

**Transport retries went 8 to 2.** They were raised to 8 that morning while chasing the above; with
the cause fixed, 8 is a spend multiplier. A 429 is refused before generation and costs nothing to
retry; a 529 mid-stream has already produced tokens, and retrying re-runs the reasoning on the
user's money. The reference allows one, transient only, because "retrying just burns another call".
The logic-level retry (`MAX_TAILOR_CALLS`) already matched at one; both are asserted now.

**CV builds were failing silently on the token ceiling.** `stop_reason=max_tokens` with zero text
blocks: the model finished reasoning and never started the document. The thinking budget is spent
*inside* `max_tokens`, and a CV is around 6,000 tokens of HTML, so 16,000 left no margin. Raised to
32,000, which costs nothing until used. Found within an hour of adding the diagnostic that
distinguishes truncation from refusal — the log previously could not.

**The recency window was read from where nobody stores it.** `posted_within_hours` lives on each
`SearchSection`; `build_source_configs` read it from the top level of the config. Every generator
yielded zero, `max()` fell through to its default, and every harvest ran at 72 hours regardless of
what anyone asked for. The test added alongside the `hours_old` fix did not catch it, because it
asserted only that two keys agreed — they did, both at the default.

**The age filter never reached LinkedIn, Indeed or Google.** Those pass `hours_old` to the scraper
and ignore `max_age_hours`, which was the only key being set. The reference sets both and its
comment says why. Currently moot in production: `DEFAULT_ENABLED` is seven no-auth REST sources and
the JobSpy tier is not enabled at all — every LinkedIn row in the corpus arrived in one import on
2026-07-27.

**Chrome printed its own header onto every CV with margins.** `--print-to-pdf-no-header` is the old
flag name and current Chromium ignores it; an unrecognised flag is not an error. The symptom is a
date, a title, a `file:///` path and a page number on a document sent to an employer. It hid because
`@page { margin: 0 }` left no margin box to draw into — which was itself the bug giving page two no
top margin. Two defects cancelling out. Both fixed: margins onto `@page`, where they repeat, and
both flag spellings passed.

**A saved proxy URL never created a `proxy_credentials` row**, so `acquire_lease` had nothing to
grant and the LinkedIn tier skipped the account silently, forever.

**Excluded keywords stopped applying once a model was trained.** They were read only by the
cold-start keyword scorer, so a term the user typed specifically in order not to see it came back
the day their first training finished. Now a hard gate at delivery, on title and company only —
matching the description turns "no recruitment agencies" into a filter that eats most of a feed.

**A delivered job was shown as borderline.** The card called anything under 80 a yellow dot while
delivery sent everything at 60 and above, under a comment claiming the two matched. One number now,
passed from the send decision into the card.

**The score schema was rejected by the API on every call.** `additionalProperties: false` is
required on every nested object and was on none of them. Invisible because the account's labels were
migrated rather than produced here, so a permanently broken call looked exactly like an empty queue.

### What was built to find them

`libs/scoring/cveval.py` — the reference's objective checks, ported. `eval_cv.md` opens by saying
keyword coverage and bullet metrics are already computed and must not be re-graded, and the judge
was being handed a character count. A model told a measurement exists and then not shown it does not
abstain; it estimates. Giving it the real numbers moved scores about 11 points. The module is
careful about what it claims: **it is not an ATS**. It measures what holds whether screening is
boolean search or a learned ranker — is the text extractable, are the posting's own terms present —
and none of it is a target, because optimising for exact-term coverage *is* keyword stuffing.

`deploy/scripts/calibrate_judge.py` — three probes whose ordering is not in doubt: tailored over
untailored, the right job over a distant one, real over a mechanically degraded copy. All three pass
by 23 to 51 points, so an A/B run on this judge measures something real. Its first version failed
the wrong-job probe, and the *experiment* was wrong rather than the judge: both jobs were drawn from
the same band above the delivery threshold, which for this account meant three variants of one role.

`deploy/scripts/ab_prompts.py` — holds everything constant except the prompt bytes, and reports the
weakest dimensions plus the judge's own fixes ranked by repetition, because a mean says a prompt is
losing and not what to change about it.

`libs/obs/timing.py` — per-stage durations with the slowest named explicitly, and end-to-end
freshness at delivery from two clocks: age from posted, which is what the user experiences, and from
first seen, which is what the platform controls. No stage duration predicts the second, because most
of the latency is spent *between* stages.

### Feed correctness

**The age filter could only drop what it could date.** Of 216 jobs pending delivery, 68 carried a
`posted_at` — LinkedIn reports none to the listing API. The front of the queue is now dated
immediately before selection, on the user's own proxy, capped at about one digest, which is the
order the reference uses. First run after the fix: 33 dropped as too old and 17 as closed, where all
50 would previously have been sent.

Also ported or added: `libs/corpus/ranking.py` (opportunity ordering — youth and scarcity, with fit
as a tiebreaker only), `libs/corpus/langfilter.py`, the per-user age window applied at delivery, and
closed-posting detection stored on `corpus.closed_at`.

### Prompts

**Role angles are per user now.** The shipped `tailor_cv.md` still argued in one person's
vocabulary — two role lenses, and adjacent-skill examples naming FastAPI and Polars. Lifted into
`08-role-angles.md`, written by `/setup-role-angles`, injected at build time where `<<ROLE_ANGLES>>`
sits. Measured indistinguishable from the inline version (+1.7 across six jobs), so the split costs
nothing and a new user gets a mechanism that provably works.

**One prompt hypothesis was tested and falsified.** A variant capping length at one page while
cutting the competencies block scored 3.8 *below* current, and its own feedback said why: "far too
thin — one visible role, no education section". Cutting the keyword wall is right; capping total
length at the same time took the space away instead of moving it. The competencies finding is
confirmed on the *winning* variant, whose most-repeated fix is to cut it.

### Two of the instruments were themselves wrong

The judge did not know what day it is, and twice called a current role "a credibility killer — a
future start date". A CV is mostly dates, so a judge without a today marks down the most recent work
on every candidate, in the dimension the holistic score weights most heavily.

And the ranking tests expired overnight: fixtures pinned to a fixed date while `select` had no
notion of now, so every check inside it read the machine clock independently. `select` takes `now`
now — a caller that cannot say what time it is cannot be tested about time at all.

---

## 2026-07-28

### Delivery is on, and two things the first real build found

The end-to-end path works: a Build CV tap enqueued, `docgen` claimed it, one Haiku call, Chrome, and
a one-page PDF arrived in the right chat 105 seconds later. `applied_at` set, the corpus row pinned,
the scratch directory gone. Both timers and the notifier are running.

**The notify dispatcher had no schedule.** `services/notifier` claims from the `notify` lane and
nothing put anything in it — `--tick` existed and was wired to nothing. Starting the worker would
have delivered exactly nothing, while looking healthy: container up, no errors, no messages.
`deploy/notify-dispatch.{service,timer}` now runs a pass every five minutes.

**A profile edit re-versioned the user, and `rubric_relevant_digest` was written to prevent that and
never called.** `save_profile` bumped `profile_generation` unconditionally, so adding a contact
detail would have invalidated every label — 3,310 of them on the account that found this — and
triggered a paid relabel to answer a question that had not changed. The digest is now compared
before the bump, and the facts block is rewritten either way, because a new link belongs on the next
CV rather than waiting for a change that happens to re-version.

The digest also hashed the whole `identity` object while the rubric substitutes `identity.name` and
reads nothing else about a person. So the field it was most likely to be asked about — a link, a
phone number — was the one it treated as rubric-relevant. The paths are dotted and resolved now, and
a typo raises rather than silently dropping a field from the hash.

**Found by the same build: an empty `links` map produces a CV nobody can reply to.** The ATS check
said so — "no email address in the extracted text" — which is exactly the warning it was written
for, on the first real document it saw.

### The overview follows the onboarding pass's rules

Three bordered panels of equal weight, which meant the thing to do next looked exactly like the
delivery settings — and the whole argument for this page is that the order is **not** guessable:
processing invalidates every label, relabelling costs real money, training is free. A page that
presents one next action and then frames it identically to a settings readout has given the
information back.

Now the state is the headline, the server's explanation is the line under it, the one action is the
only control on the page, and everything else is quiet text at the bottom. The three numbers stay —
they are the answer to "is this working" — as figures rather than a table, because a table implies
rows worth comparing and these are three unrelated facts.

The split between what the server says and what the page says is the same one the wizard uses: the
server owns the explanation (`next.message`), the client owns the four words above it. Cards remain
where a page really does hold several unrelated things: the feed, the privacy disclosure, settings.

### docgen: a tap becomes a tailored PDF (Phase 5b)

The `docgen` lane has existed since Phase 1 with **no worker** — the same shape of gap the `score`
lane turned out to be. "Build CV" taps enqueued correctly and waited. `services/docgen` is the
worker: one Claude call on the user's own credential, Chrome for the PDF, a Telegram upload, and a
scratch directory that does not outlive the job.

**Python does the mechanics and the model makes one call**, which is the reference instance's
measured design: its agentic build re-read the profile, the skills and the template every turn, and
that loop rather than the writing was most of the cost.

Four things are enforced after generation rather than asked for, because each is a rule the prompt
already states and the model still breaks:

- **The CSS is re-injected from the template.** Stripped before sending, restored byte-for-byte
  after — so "the model only changes text" is true rather than requested, and the layout is always
  the platform's whatever came back.
- **Em-dashes come out of prose and bullets.** The prompt's most-broken rule; they are the model's
  default habit and its self-check is aspirational.
- **Hidden content is stripped, and then the result is scanned.** If anything invisible survives,
  **the CV is not delivered at all.** Invisible keyword stuffing looks perfect and gets the person
  filtered out — or blacklisted — by the system it was aimed at, and they would never learn why.
  Failing closed is the recoverable outcome.
- **The page count is measured from a real PDF**, not estimated from the markup, and a CV over two
  pages gets exactly one revision. The instruction says *cut*, never *shrink*: a model told to
  shorten will otherwise reduce the font, which produces an unreadable CV — and the CSS is restored
  afterwards anyway, so the attempt is silently undone and the length unchanged.

**Deliver, then record.** A crash between them costs a duplicate delivery; the reverse order costs a
user who thinks they applied and did not. `applied_at` is set by the first build and never moved,
and the corpus row is pinned so retention cannot remove the description out from under it — the
durable half of an ephemeral document (D9).

**The quota is checked at the tap, not in the worker** (`TENANT_SAFETY.md` §3). A limit checked
halfway through a build has already spent the call it was protecting, and the popup names the limit:
a button that silently does nothing is how somebody concludes the product is broken. `jobs_today`
counts enqueued jobs rather than successful builds — a failed build still spent the money.

Two supporting changes: `libs/llm` grew `complete_text`, because a tailored CV **is** an HTML
document and asking for one inside a JSON string field would have the model escape a few thousand
characters of markup by hand. And the template's fonts are now resolved by name through fontconfig
instead of by relative `@font-face` url() — `Dockerfile.docgen` installs `fonts-lato` and fails the
build if `fc-list` cannot see it afterwards, because the failure it replaces was Chrome silently
falling back to Arial.

**Built, not deployed.** The image has never been built on the box. 730 passed.

### The two tailoring prompts stop being about one person

**SACRED-prompt change, approved 2026-07-28**, and the approval was for exactly this: remove the
reference instance's specifics, keep every rule. Both prompts already read `<<NAME>>` and
`<<LANGUAGES>>`; what remained named no person and was still about one — "cut the Quantitative
Research — Global Liquidity entry", "keep the quant tooling (Bloomberg, ByBit API)", "rename Crypto
Screener", a metrics list of one CV's numbers, and a Hungarian formal-address rule in the cover
letter complete with its grammar.

Dead instructions for most users and an active hazard for the rest: told to keep the quant tooling,
a model writing for somebody who has never touched a trading terminal will still find something to
put there. Each became the general rule it was a special case of — cut pure-research entries for an
engineering role, keep the specialist tooling **the profile states** for a specialist one, use the
formal register of whichever language the letter is written in.

The guard test grew a second half. It asserted no *name* survived, which the cover letter passed for
a day while carrying "HUNGARIAN" in capitals — the check was case-sensitive — so it now scans for
the specifics too, word-bounded, and case-insensitively for the language rule.

Also fixed: the subject-verb agreement the original find-and-replace left behind ("the concrete work
they describes", "the single most relevant thing they has built"). A prompt written in broken
English is a prompt whose style rules the model has less reason to respect.

### The CV template is rendered from the profile (Phase 5b)

The prompts were generalised earlier today and **the template was not**, which left the worst of the
two half-states available: a docgen worker built on top of it would have produced a correct-looking
CV carrying the reference instance's employment history under a real user's name. The identity was
already out of the header; this is the body.

`libs/templates/cv.py` fills eight markers from `ProfileSchema` — the same deterministic-render job
as `rubric.py` and `facts.py`, and for a sharper reason: the rubric decides what a user is shown and
the facts block grounds a generation, while this ends up as a PDF attached to a real application.

**What it produces is the input to tailoring, not the output.** `tailor_cv.md` receives the rendered
CV as its BASE TEMPLATE and cuts to the 4–6 entries that earn their place for one job. So the
renderer's job is completeness and truth; it selects nothing.

Three decisions inside it:

- **A section with no data is omitted whole**, not left as an empty heading. The prompt is told to
  keep the template's sections, so an empty "Projects" title survives tailoring into the PDF.
- **`Certifications` became a schema field.** It was cut on the first pass for want of one, and
  that was wrong in the direction that matters: the section disappearing silently removes a
  credential the candidate actually holds. `ProfileSchema` version 2 adds `certifications`
  (name, issuer, year, all free text), capture fills it with no prompt change because the model
  is handed the schema itself, and the facts block lists them — which is what makes "never
  invent a certification" enforceable rather than a hope. A certification is the most checkable
  claim on a CV: an employer can verify it with the issuer in a minute.
- **`projects[]` got its own section.** The user answered `experience` and `projects` separately;
  a project presented as employment is the kind of overstatement that ends an interview.

Everything from the profile is HTML-escaped: capture reads uploaded documents, which are untrusted
text, and the output goes to a headless Chrome. `links` values are only turned into anchors when
they look like a URL or an email — a phone number in that map was otherwise a header that looked
right and a dead link in the PDF.

Still open, and now written down where the next change will hit it: the `@font-face` paths point at
the reference instance's tree. Nothing renders a PDF yet, so the fix belongs in the change that
builds the docgen image. Chrome would fall back to Arial silently. 694 passed.

### The feedback loop (Phase 5a)

`user_labels.source = 'feedback'` at `weight = 5` has been in the schema since Phase 2 with nothing
writing it. **This is the half that makes the product improve** — everything upstream ranks jobs by
what a model thought against a rubric, and this records what the person actually did, which is a
decision rather than an opinion.

**A webhook rather than polling**, now that the API is publicly reachable through the tunnel: one
request per tap instead of a per-user `getUpdates` loop with its own worker and offset bookkeeping.

**Three things stand between a stranger and somebody else's training data**, and none is redundant:

1. a **per-user path secret** — every user runs their own bot, so the URL is what says whose tap it
   is; a shared path could only answer that from the request body, which is attacker-supplied;
2. Telegram's **`secret_token` header**, compared in constant time — a path leaks into proxy logs,
   browser history and screenshots, and the header is what makes a leaked path insufficient;
3. **the tapper must be the account owner.** A feed channel has other members. Without this, anyone
   who joined to browse jobs could teach somebody else's model — not a privacy leak but a slow,
   invisible corruption of the one thing the product is built on.

**Applied is 90, not 100.** People apply to things they are unsure about; saturating the scale would
teach the model that every applied job is identical and throw away the ordering that makes a feed
useful. A second tap does not move `applied_at` — that column is the date somebody actually applied.

**The digest became one message per job, reversing an earlier decision.** Batching avoided a burst
of notifications, which was a good argument that lost to a better one: a Telegram keyboard attaches
to *a message*, so a digest structurally cannot carry per-job buttons. Acting on a match is worth
more than saving nine notifications, and the burst is still bounded by the per-delivery and daily
caps. The reference instance's watcher sends per-job cards for the same reason.

The notifier renders the keyboard and the API parses the taps, and they may not import each other —
`callback_data` is the entire contract between them, so a test reads both sides and asserts they
agree. Migration `b8e2d05f713c`. 657 passed.

### Two deployment bugs the containers found, and the drill did not

Both were invisible until something actually ran in a container.

**Artifact paths do not survive leaving the host that wrote them.** `models.path` records an
absolute path from the process that trained the model — a script on the host — while the reader is
a container with the artifacts mounted somewhere else. The scorer crash-looped on `FileNotFoundError`
for a path that was perfectly correct on the machine that produced it. The filename is now the
identity and `MODEL_DIR` is the location; a stored path that does exist is still used unchanged, so
host-side scripts keep working.

**The restore drill did not catch this**, and it is worth saying why: it checks that a `models` row
is servable — the version columns agree — not that the file behind it resolves. On a restore to a
new host it would have reported a healthy platform that could not score. Worth fixing in the drill.

**`:?` in compose blocks every command, not just the service that needs it.** A required
`TUNNEL_TOKEN` made `up scorer` fail with a message about cloudflared, twice. The rule now written
down: `:?` only where absence is *silently* dangerous — a worker that starts without `SECRETS_KEK`
claims jobs and dead-letters every one — and `:-` where the process fails loudly on its own.

### A CI job that installs each service alone and imports it

The same bug shipped three times in one day: `httpx` missing from `api`, then `cryptography` and
`httpx` missing from `scorer` and `trainer`. All are reached *indirectly* — `libs/obs` imports
`libs/secrets`, which needs cryptography; `services/scorer/serve.py` imports
`libs/contracts/embedder`, which imports httpx — so a development environment with everything
installed cannot see the gap, and the failure lands as a container that will not start.

The new `service-deps` matrix installs each service's requirements **alone** and imports its
entrypoint. It caught the second missing dependency within a minute of being written, on a service
I had already "fixed".

`harvester` was relying on cryptography arriving transitively through another package. It works
today and would break on any dependency bump that dropped it — declared explicitly now.

### The `score` lane — closing the gap between harvest and delivery (Phase 4c)

The harvest cycle stored and embedded; the notify worker delivered what was already scored. **Nothing
turned a corpus row into a score.** Both live accounts were 398 rows behind and growing at the
harvest rate, and the monitoring could not have caught it: once the scored backlog is delivered,
`pending_matches` falls to zero and the silent-feed alert goes quiet, because from outside a feed
with nothing to send looks exactly like a feed with nothing new.

**A lane rather than a call from the notifier**, because `services.notifier` may not import
`services.scorer` — and here that contract is load-bearing rather than ceremonial: the scorer's
fail-closed version checks belong in the one deployable that owns them. A lane also inherits
round-robin fairness, so one account's backlog cannot starve another's.

**A missing model is not a failure.** The harvest cycle enqueues for every active account and a new
signup has no artifact until its first train; dead-lettering those would fill the queue with red for
the one state every account passes through. The worker logs it at info and the next cycle asks again.

The rest is the same shape as the other lanes: one queue job is one batch, the continuation clears
its own dedup key before re-enqueueing, and `scored_model_version` makes a re-run cheap while a
retrain correctly invalidates everything at once.

**Deploy artifacts for the remaining four services** — `api`, `notifier`, `scorer`, `trainer` —
behind a `full` compose profile, so a bare `up -d` still starts only Postgres and the two credential
workers. Starting delivery messages real people, and that should be a decision rather than something
that happens because somebody typed `up`. The API publishes **no port**: it is reached through the
Cloudflare Tunnel (D7), so the VPS needs no open inbound port at all.

Migration `a7e3f9c14b62`. 638 passed against a real Postgres.

### Backups, and a restore drill that passed (Phase 4b)

Postgres is the single point of failure by design, and under D4 it is self-managed — no provider is
taking backups off anybody's hands. Nightly `pg_dump`, verified as it is written, 30-day retention.

**The KEK is deliberately not in the backup, and `backup.service` cannot read it.** Two silent ways
to get this catastrophically wrong: the key beside the dump makes the backup a complete credential
breach in one file; the key nowhere makes a restore a database of undecryptable secrets and every
user re-enters every credential. The surest way to keep them apart is for the process that writes
one to be unable to read the other.

A partial dump is deleted rather than left behind — a file that looks like a backup is worse than
no backup, because somebody will find it and believe it.

**The drill passed against a real 20 MB backup of the live platform.** It checks the three things
that have to be true afterwards, not just that the file loads: row counts match the live database
(2 users, 4,287 corpus rows, 3,474 labels), a stored `telegram_bot` credential **decrypts with the
separately-held KEK**, and 2 of 2 models come back servable. The second is the entire KEK-separation
design under test, and the only way to learn it works is to try it.

**It found a bug on its first real run**, which is the argument for having a drill rather than a
procedure. `subprocess` wants a real file descriptor for `stdin`, and `GzipFile.fileno()` returns
the descriptor of the *compressed* file — so psql was fed raw gzip and failed with
`invalid byte sequence for encoding "UTF8": 0x8b`, which reads like a corrupt dump rather than a
mistake in the restore path. A backup nobody has restored is a hope.

### Monitoring and alerts (Phase 4a)

**Every failure on this platform is silent** (`OPERATIONS_PLAN.md` §1). A stalled queue, a harvester
finding nothing, a retention trim that quietly stopped — none of them raise, and each is otherwise
discovered weeks later by a user asking why their feed went quiet. `libs/ops/health.py` makes them
numbers, `libs/ops/alerts.py` decides which need a person.

**The design is entirely about not crying wolf.** An alert that fires for a condition nobody acts on
trains its reader to ignore the next one, and the next one is the outage. So:

- the silent-feed alert requires **matches waiting** as well as no delivery. Without that qualifier
  it fires constantly for anyone with a narrow config, who is not broken — they simply have nothing
  this week;
- a stall is measured against **that lane's** normal wait, not a fixed number: a `notify` job
  waiting an hour is broken, a `label` job waiting an hour is a queue doing its job;
- one dead-lettered job pages, because the queue retries five times before giving up — one dead job
  means five failures nobody saw;
- there are two severities, not three. A third becomes the one everybody filters out.

Every alert names an action. "Look into it" is not one.

**The exit code is the interface** for `check_health.py`: 0 nothing, 1 warnings, 2 a page. It emails
too, but a monitoring system whose own failure is silent is exactly what this section exists to
prevent — so `health.service` sets `SuccessExitStatus=0 1`, which makes a page a unit failure that
`systemctl --failed` shows even with mail down.

**`GET /admin/health` and `/admin/users`**, read-only. `OPERATIONS_PLAN.md` §5 describes an
action-capable console; shipping the reading half now makes failures visible before the first
invited user, and shipping half of the acting half would be worse than none. Admin is
`ADMIN_EMAILS`, read per request — granting it is a deploy, which is the friction it should have,
whereas a boolean column is one UPDATE away from privilege escalation audited in the database it
grants access to. An empty variable means **nobody**, not everybody. Non-admins get 404 rather than
403: a 403 confirms the endpoint exists and that somebody is worth phishing for access to it. The
user list carries no credential material at all, not even `last_4`.

### Monochrome, with a theme toggle

Black, white and grey. Two colours survive — a red for errors, a green for a score — and both mean
one thing each, which is why they read instantly; a red among six other hues is decoration.

**Light and dark on every page**, including signed-out ones. Three states rather than two: `light`,
`dark`, and unset, with unset the default so a dark-system viewer gets dark without being asked.
Pressing the toggle leaves unset permanently — a three-way control is a puzzle in a corner nobody
wanted to solve.

**The theme attribute is written before first paint** by a snippet in `index.html`. A React effect
runs after the first frame, so a dark-mode viewer would have seen one white flash on every load —
the detail that makes the difference between a toggle that feels built in and one that feels bolted
on. Transitions are colour-only at a single 140 ms duration, and `prefers-reduced-motion` removes
them.

Andras's four candidate-profile files imported alongside his documents: nine documents, 204,388
characters.

### Both reference owners' documents imported

`import_documents.py` — everything the upload form does, for a folder: same extractor, same content
hash, same replace-on-re-upload, and the file itself never stored. Andras's five (a master CV plus
four long-form context files, 158,975 characters) and Ruben's four candidate-profile files (28,949).

**What was deliberately left out, and why.** Ruben's `documents/` folder is empty — his profile
content lives in the assistant's skill files — so the import took the four that describe *him* and
skipped `04-job-evaluation.md` (that is the rubric, already migrated as `rubric_md`), the CV and
cover-letter templates (templates, not documents about a person), and the `.html` base CVs. Feeding
template markup to the processing step would produce a profile built partly from HTML.

### Per-category Telegram channels

A single feed of everything is a feed people stop reading. The reference instance splits it by the
distinction its owner actually makes when scanning — jobs at home, jobs on LinkedIn, everything
else — and that split is now a platform feature: `users.telegram_routes`, JSONB, every route
optional (`f1d84c2e9b07`).

**The ordering is the part that is easy to get subtly wrong, so it is ported verbatim and tested.**
Hungarian jobs win first, *including a LinkedIn job in Budapest* — somebody who wants local roles
wants them wherever they were posted, and routing that one to the LinkedIn channel hides it in the
stream they skim least. LinkedIn wins second, as the highest-volume source worth isolating.

**A missing route is a fallback, never a drop.** Anything unrouted goes to the personal chat, so an
account with no routing table behaves exactly as it did before this existed. Routing is resolved
per job rather than per digest: deciding once for a batch would put a Budapest job in the LinkedIn
stream whenever it happened to be batched with one.

One limitation asserted rather than hidden: the Hungarian match is a substring on the location
string, so "New Budapest Restaurant Group, Chicago" routes to the HU channel. Parsing a free-text
location properly costs real Budapest jobs whenever the parse disagrees; the bug costs one misfiled
job in a channel the user reads anyway.

`set_telegram_routes.py` asks Telegram whether the bot can actually see each channel before storing
it — a channel the bot is not an admin of accepts the configuration and then rejects every
delivery, which the user experiences as an empty feed.

### `set_credential.py` — storing a credential before the API is deployed

`/credentials` is the real path and this does not replace it. It exists because the `api` container
is not on the box yet, and the two migrated accounts cannot supply a Telegram token without it —
which meant nothing could be delivered to either.

**The value is prompted for, never taken as an argument.** A credential on a command line is in
shell history, in `ps` output for every user on the box, and in any tracing that happens to be on.
`--stdin` covers scripted use without any of the three.

Validated against the provider by default, as the endpoint does, and the Telegram chat id is
captured automatically — the step that confuses people more than the rest of setup put together.

### The web layer (Phase 3 step 12) — Phase 3 complete

React + TypeScript on Vite, ready for Cloudflare Pages behind the Tunnel (D7). Seven onboarding
pages, dashboard, feed, profile confirmation, settings, and the public privacy page.

**`openapi.json` and `src/api-types.ts` are committed, and CI regenerates and diffs them.** That is
the mechanism `web/README.md` specified in Phase 0 and it earns its keep immediately: changing a
response shape without regenerating fails the build with the command to fix it, instead of
surfacing as a runtime `undefined` in front of a user weeks later with nothing pointing at the
commit that caused it.

**Two runtime dependencies, 69 kB gzipped.** No router — this is a wizard plus four screens, and
`react-router` would be more configuration than routing. No component library — what one would have
given us is a hundred lines in `src/ui.tsx`. A hash router rather than history, so Pages serves
`index.html` for every URL with no rewrite rule to forget.

**Nothing in it re-derives a server rule.** Which onboarding page you are on comes from
`GET /onboarding/state`; what the account needs next comes from `GET /dashboard`. Those rules live
once, in `libs/onboarding` and `services/api`. A copy here would be the one that goes stale, and the
first symptom would be somebody stuck on a page they had already finished.

**One refresh at a time** (`src/api.ts`). A dashboard load fires several requests; if each refreshed
on its own 401 the second would replay a token the first had rotated — and the server treats a
replayed refresh token as theft and revokes the entire session family. Every caller waits on one
shared promise.

The web CI job turned out to be a **second check for free**: it installs only
`services/api/requirements.txt` and then imports the app to dump the schema, so a dependency the
service imports but does not declare fails there. It caught `httpx` on its first run — declared by
the harvester and notifier, used by `libs/credentials`, and missing from the API's list, which
would have shown up as the `api` container failing to start on the box.

Known gap, written down rather than glossed: FastAPI types most handlers as a bare `dict`, so the
generated types cover paths and request bodies but not response *contents*. The response shapes the
components read are narrow hand-written views at the bottom of `src/api.ts`. Annotating every
handler with a response model closes it and is the right fix if that drift ever bites.

### Deploy artifacts for the two credential-spending workers

`capture` (the `process` lane) and `labeller` (the `label` lane) now have Dockerfiles and compose
services. Separate images despite identical dependency lists: they are separate lanes with separate
restart cycles, and one worker's dependency bump should not rebuild the other
(`CODE_STRUCTURE.md` §5).

`SECRETS_KEK` is `:?`-required for both. Neither can do anything without decrypting the user's
credential, so starting without it would produce a worker that claims jobs, fails every one, and
dead-letters a user's queue — a failure that reads as a provider outage.

Also corrected `services/capture/requirements.txt`, which still described a Managed Agents client
three commits after that client was deleted.

**Not applied on the box.** SSH from the build machine is refused, so the three pending migrations
and the `up -d` for both workers are written down in `deploy/README.md` with the reason each is safe
to apply late, rather than left as something to remember.

### The GDPR obligations, as behaviour (Phase 3 step 11) — Phase 3 backend complete

All three are things the product does now rather than things a document claims.

**`GET /legal/privacy` names each processor** — Anthropic, Telegram, the proxy provider, with what
each receives, what for, and whose credential pays. Public, because signup links it before anyone
has an account. Served from `libs/legal` so the policy and the consent screen cannot drift; a test
asserts they are literally the same object, because the drift would be invisible and a consent
recorded against a disclosure that no longer describes reality is worse than no consent screen.

**Consent at *process data*, not at upload.** The obligation was written when capture uploaded files
to Anthropic's Files API and it named upload as the moment. Files no longer leave this server, so
nothing crosses a processor boundary until that button is pressed — asking at upload would ask about
something that had not happened and be stale by the time it mattered. `/process` returns 403 without
it, checked in the request rather than in the worker where the refusal would be invisible for hours.

It is **version-compared**, not merely present: `users.processing_consent_version` records which
disclosure was agreed to, so naming a new processor makes every stored agreement stale by
construction. That is the point, not an inconvenience to route around.

**Deletion, proved rather than asserted.** `DELETE /account` requires the password — irreversible,
and a stolen session cookie must not be enough. The test enumerates every table in the metadata
carrying a `user_id` column and asserts none survives, so adding an eleventh table and forgetting
its cascade fails there rather than leaving a stranger's rows behind for a year. The DEK is
crypto-shredded in the same request: a backup restore that resurrected a deleted user's Claude key
is the worst outcome a delete request could have.

One bug caught by the tests: `verify_password(stored, password)` was called with its arguments
reversed, which would have rejected every correct password at the deletion gate.

Migration `e5c9b71a4d38`. **Phase 3 steps 1–11 are done**; step 12, the web layer, is what remains.

### The dashboard and a stage-aware feed (Phase 3 step 10, backend)

`GET /dashboard` in one call, because the panels are not independent: "you have 3,310 labels" and
"your model is stale" contradict each other if they are read a few seconds apart while a worker is
running. It reports what the account needs *next* rather than offering four actions and hoping they
are pressed in order — the order is not guessable, and a user who gets it wrong pays for it.

`GET /feed` says which stage ranked it. Before a servable model exists it is keyword matching, and
presenting that as "your model" would make the product look broken when the real model arrives and
the ordering changes completely.

**The design correction the tests found.** The dashboard first branched on live onboarding
completeness, so breaking a Telegram token threw a fully onboarded account back to the signup
wizard — hiding the only screen that explained what broke. It now branches on the stored
`onboarding_completed_at`, which is exactly the distinction that column exists for, and regressions
surface as banners.

A stale model is **named**: "your profile changed after this model was trained, so your jobs need
relabelling and a retrain". A greyed-out button is not something a person can act on.

### Cold start: a feed in 30 seconds, a model in 30 minutes (Phase 3 step 9)

**Stage 0 — `libs/scoring/rules.py`.** A score from the config alone: no model, no LLM, no
embedding. It cannot tell a good backend role from a bad one, because nothing has told it what good
means yet. Its whole job is to be non-empty and not obviously wrong in the seconds after somebody
saves a config, and that is worth building because the most common thing a person does with an
empty page is close it.

**Stage 1 is not built, and that is the decision.** A bootstrap model trained on other users'
labels is a data-use question needing consent; one trained only on the operator's labels is a
separate artifact to build and version. Stage 0 covers the gap it existed to cover, for free and
with no consent surface. Recorded rather than omitted.

**Pool selection** (`services/labeller/pool.py`) bands the rules-ranked corpus 40/40/20 and draws by
**striding through each band, not taking its head**. The head of the high band is 120 near-identical
jobs — often the same employer and phrasing — which is 120 labels' worth of a user's money for one
label's worth of information. The pool is re-selected per batch rather than stored once: it costs a
ranking pass and buys a balanced batch *every time*, so an interrupted cold start leaves a usable
training set rather than 200 likely-positives.

**The `label` lane has one job kind** and `coldstart.next_batch` decides what it means: stale labels
are carried forward before new ones are bought, because jobs the user already cared about are
cheaper per unit of signal. Two job kinds would make every caller re-derive that ordering, and the
first one to get it wrong spends somebody's money re-buying labels they had. When the labels are in,
the worker queues the train itself — 300 fresh labels and no model because nobody pressed a third
button is not a state to leave a person in. A partial run still trains at 100 labels
(`COLD_START_PLAN.md` §7).

### The relabeller, and one shared Anthropic client (Phase 3 step 8)

`services/labeller` scores jobs against a user's rubric with Haiku, in the `label` lane, on that
user's own credential. It is what makes editing a profile survivable: a profile change bumps
`profile_generation`, every label written under the old rubric answers a different question, and
without this the account has **zero** usable labels and cannot train at all.

Three decisions, each of which had a wrong-looking obvious alternative:

- **One queue job is one batch of 50.** The worker commits the handler's effect and the job's
  completion together and forbids a handler from committing its own transaction, so a queue job's
  size *is* the unit of work a crash destroys. At 200 an interrupted worker throws away four Haiku
  calls the user paid for. The re-enqueue chain also lets round-robin interleave two users'
  relabels rather than running one to completion.
- **The continuation clears its own dedup key first.** `enqueue` collapses against any job in
  `ready` *or* `claimed` with the same key — and the running job holds exactly the key its
  continuation needs. Left alone the continuation is silently dropped and a four-thousand-job
  relabel stops after fifty, which looks identical to success.
- **Scores are matched back by URL, never by position.** A model that reorders its output would
  otherwise shift every label onto the wrong job: a training set that looks fine and teaches the
  opposite of what was intended.

Confirming a profile now queues the first batch rather than leaving it to a second button — an
account that cannot train is not a state to leave somebody in because they missed a click.

**`libs/llm`** — the Anthropic adapter moved out of `services/capture`, because two services now
call Claude on the user's behalf and services may not import each other. A copy in each is how the
OAuth header ends up correct in one and missing in the other.

**`libs/queue` now exports its API.** It exported nothing, so `from libs.queue import enqueue` was
an ImportError that took down every API route — see below.

### A local Postgres, and the three things it immediately caught

Docker Desktop plus a throwaway `postgres:16` is now the pre-push check: `alembic upgrade head` and
all **553** tests, rather than the 300 that run without a database. The first run found three
things the CI-only loop had been hiding:

- **The suite exhausts Postgres's connection slots.** 69 `create_engine(...)` calls across 22 test
  files, one per function-scoped fixture, and `QueuePool` returns a connection to the pool rather
  than to the server on close — so a few hundred tests leave a few hundred idle connections and the
  run dies at "sorry, too many clients already". It surfaces as unrelated tests failing at *setup*,
  which reads like flakiness. `tests/conftest.py` defaults engines to `NullPool`, at import time
  rather than in a fixture: a fixture runs after collection, by which point a module-level
  `from sqlalchemy import create_engine` has already bound the original.
- **A test asserted on `record.message % record.args`** — already-formatted, so any log line with no
  args raised `TypeError` instead of asserting.
- **`now()` is transaction-start time**, so a test that set `processed_at` and then uploaded a
  document in the same transaction stamped both identically and could never observe the change it
  was testing. Production never hits this — the worker and the upload are separate transactions —
  so the fix is in the test, with the reason written down.

### Two CI failures, and the checks that would have caught them

Both were pushed without local verification, because the machine has no Postgres: migrations and
253 DB-backed tests skip silently there, and "300 passed" reads like a green run.

1. **`ck_jobs_ck_jobs_lane_valid` does not exist.** `libs/db/base` sets a naming convention of
   `ck_%(table_name)s_%(constraint_name)s` and Alembic applies it to whatever the migration passes,
   so handing it the already-rendered name double-prefixes it. **Catchable with no database:**
   `alembic upgrade <rev>:head --sql` renders the SQL offline. Now run on every migration.
2. **Every API route broken at import** by `from libs.queue import enqueue`. Invisible locally
   because the tests that import the app are the DB-backed ones. `tests/test_entrypoints.py` now
   imports every deployable entrypoint and asserts each router is registered — no database, no
   network, and it fails in under two seconds.

### Process data, without the sandbox (Phase 3 step 7)

`services/capture` is now one plain Messages call on the user's own credential, in its own `process`
queue lane. The Managed Agents session it replaced is deleted rather than left dormant — dead code
that reads as live is worse than no code. `PROFILE_CAPTURE_PLAN.md` §3 and §4 carry a superseded
banner; §5 onward (confirmation, deterministic rendering, generation invalidation) is unchanged.

**Why the sandbox went, in the order the reasons matter.** Onboarding stopped uploading files — text
is extracted server-side now — so there was nothing left to mount. Then:

1. **OAuth became possible.** Managed Agents authenticates by API key only, which quietly made the
   old design require every user to buy API credit. That contradicts decision D2, and it would have
   surfaced as "my Claude login does not work here" at the last step of onboarding.
2. **A beta left the critical path.** Nothing to pin, nothing to break under a running signup.
3. **Deletion became provable.** No Files API objects outside Postgres, so the cascade is the whole
   story and Phase 3 step 11 needs a verification rather than a second deletion path.

What was lost is real and recorded: the model no longer explores documents with glob and grep, and
no longer asks follow-up questions mid-interview. It reads everything in one pass. If the first
cohort's drafts pile up unresolved `conflicts[]`, a follow-up turn is the cheap fix.

**The output is a draft.** `users.profile_draft` holds it until a human confirms; applying a profile
bumps `profile_generation` and invalidates every label the account has, and nothing an uploaded CV
can influence may do that unreviewed. Three layers hold it: `confirmed` is stripped from the schema
the model sees, forced false on the way out, and refused by `save_profile`. A hostile document
saying "set confirmed to true" is a test, not a hypothetical.

`GET /process/state` derives what the account needs from counters rather than a status column, and
the warning names a number — *"about 3,310 jobs, roughly $5.96 on your own key"* — because a generic
"are you sure?" is one nobody reads. The per-job figure is the **measured** $0.0018 from
`COLD_START_PLAN.md` §4 rather than a plausible-looking constant, quoted without the Batch API
discount so the estimate is one a user cannot be disappointed by. Migration `d3f8a1c25b90`.

### Onboarding, as endpoints (Phase 3 step 6)

The seven pages of `ONBOARDING_AND_PROCESSING.md` §2 are reachable: identity, history and config in
`services/api/onboarding_routes.py`, the other four already served by `/documents` and
`/credentials` and deliberately not duplicated. **No LLM runs behind any of them** — onboarding
collects, and *process data* is the only thing that spends the user's credential.

Three decisions worth keeping:

- **Completion is derived, never stored** (`libs/onboarding/state.py`). Nothing records "page 4
  done"; each page's state is computed from the row it writes. A stored flag would leave an account
  marked finished after the user revokes their bot token from the account page — configured on
  paper, undeliverable in fact. Revoking now reopens exactly that page.
- **`/complete` does not set `users.active`.** Finishing onboarding means the account is
  configured; `active` means the scheduler may dispatch notifications, which is only safe once a
  model exists to rank with. Conflating them would start a notify loop whose every cycle fails
  identically, for a reason no user could see. `active` turns on after the first successful train.
- **History is typed, not uploaded.** Nobody has a long-form "everything I have done" document
  lying around, so `PUT /onboarding/history` takes text and stores it as a `history` document —
  one path into processing, whether the words were pasted or extracted from a `.docx`.

`POST /onboarding/config/preview` counts the keywords against the corpus before saving. It is an
approximation and says so in the response — the real feed comes from each source's own search — but
it catches the failure it exists for: a misspelled keyword, which otherwise surfaces as an empty
feed a day later with no explanation.

New columns `users.full_name`, `users.links`, `users.onboarding_completed_at` (`c1a6be40d3f2`). The
name is not derived from the email: it is what appears at the top of a generated CV, and
`r.lekli@…` is not a name.

### The facts block, and a daily cap on outbound mail

**The facts block** (`libs/scoring/facts.py`, `PROFILE_CAPTURE_PLAN.md` §6.2) — the `CLAUDE.md`
equivalent for a service with no CLI to auto-load one. The single-tenant instance gets ~434 lines of
ambient grounding on every generation call; a hosted service gets none, and the 2026-07-15 A/B
measured what follows: stripped of profile context the model invented an employer and fabricated
metrics. On a hosted product that lands on a real application under a real person's name.

It is **distinct from the rubric**, and the distinction is the point:

| | Rubric | Facts block |
|---|---|---|
| Answers | *is this job a fit* | *what is true about this person* |
| Read by | the labeller, the scorer | anything that writes on their behalf |
| Needs | gates, priorities, anchors | evidence, phrasing, exclusions |
| Changing it | invalidates labels and models | invalidates nothing |

Both render from the same confirmed profile in the same write, so they cannot disagree or lag. That
last row is why `refresh_facts` exists and deliberately does **not** bump `profile_generation`:
changing what may be written about someone cannot make a past label wrong, and bumping would force a
relabel and a retrain for nothing (§7).

**A platform-wide mail cap**, rolling 24 hours, default 400 against Gmail's ~500. The provider's own
limit fails silently — messages stop arriving, nothing raises at send time, and the first symptom is
a user saying they never got a link. Attempts are counted rather than successes, because an outage
would otherwise burn the allowance twice and the provider counts attempts too. **The table stores no
recipient address**: a cap needs a count, not a mailing list, and per-address protection already
lives in the signup rate limiter — so it never becomes one more place a user's email lives, or one
more thing an account deletion has to reach into.

**A gap this surfaced, worth naming.** Both live accounts render a facts block, but with no
Experience or Projects sections — their profiles were transcribed from each instance's *rubric*,
which describes strengths in aggregate and names no employer, date, or metric. That is sufficient
for scoring, which is why both feeds work. It is **not** sufficient for generation: a CV builder
would have nothing concrete to draw on. Filling it is exactly the job §1 says a form cannot do and
capture must — read the person's actual CV. Recorded rather than papered over.

Migration `f7c2a4e8d165` adds `users.facts_md` and `mail_sends`. 472 tests.

## 2026-07-27 (night, Phase 3 step 1b)

### The `api` service — signup, verification, login, refresh, logout

FastAPI, one transaction per request, cookie policy copied verbatim from `AUTH_REUSE.md` §3.

- **The refresh cookie is scoped to `/auth/refresh`**, not `/`. The detail most implementations
  miss: scoped that way the long-lived credential is absent from every ordinary request, so an XSS
  on any other route cannot reach it. `HttpOnly` and `Secure` on both cookies unconditionally.
- **No account-existence oracle.** Signup and login answer identically whether or not the address
  has an account, and login verifies a throwaway argon2 hash when the user does not exist so the
  *timing* does not reveal it either.
- **Verification does not log the user in.** A link travels through email and can be forwarded;
  proving control of an address should not hand out a session.
- **A failed verification email does not fail the signup.** The account exists and another link can
  be requested. Making registration depend on Gmail being reachable would be worse.

**Mail is Gmail SMTP with an app password.** One sending account needs no per-user consent and the
OAuth refresh dance buys nothing at this scale. The tradeoff is recorded rather than buried: Gmail
caps a normal account near 500 recipients a day — ample for an invited cohort, to be revisited
before self-serve, and the failure mode is silent rejection rather than an error at send time.

**One real bug, caught by CI:** raising `HTTPException` discards the injected `Response`, so a
failed refresh returned 401 with **no `Set-Cookie` at all** — a client holding a replayed or expired
token would have kept presenting it forever, since nothing ever told it to stop. The failure path
returns a `JSONResponse` with the cookies deleted on it instead.

Two test-fixture lessons worth keeping: `example.test` is a reserved special-use domain and
`email_validator` correctly rejects it (the rest of the tree writes `User` rows directly and never
passes an address through validation), and cookie attributes are case-insensitive — asserting on
capitalisation tests the client's formatting, not the policy.

469 tests.

## 2026-07-27 (night, Phase 2 closes)

### Two accounts, two models, two feeds — on one stack

The remaining third of Phase 2's exit criteria. Both single-tenant instances' labelled history is
now in the shared corpus, and both accounts have their own confirmed profile, rubric, model, and
feed.

```
lekliruben@gmail.com          andraslekli@gmail.com
  23 hard gates                 15 hard gates
  2,529 labels @ gen 1          945 labels @ gen 1
  model v1, servable            model v1, servable
  55 core / 24 off-stack        52 core / 26 off-stack
  3,892 scored                  3,892 scored
  345 high, 804 review          64 high, 207 review

corpus: 3,896 rows, 3,892 embedded
overlap of the two top-100 feeds: 0
```

**Zero overlap is the result worth keeping.** Same corpus, same embeddings, same feature columns —
and the two models rank them disjointly, because the token lists behind `core_stack_count` and
`offstack_count` come from each person's own profile. That is §6.3's whole argument, demonstrated
on real data rather than asserted.

**Retrained rather than imported.** The existing `.joblib` artifacts were fit against each
instance's *hardcoded* token lists; the platform derives them from the profile, so the weights would
have been attached to features that no longer mean the same thing — precisely what the scorer's
fail-closed check refuses. Same labels, same embedder, same feature definitions at train and serve
time. Comparing the platform against the old instances is now an outcome comparison, which is what
D6 wanted.

**Only labels at each reference's newest rubric were imported** — 3,310 of Ruben's 4,283, 947 of
947 for Andras. The rest were written under older rubrics and answer a different question. Ruben's
3,310 then collapsed to 2,529 distinct corpus rows: 781 labelled URLs deduplicated onto postings
already present, which is the four-layer dedup doing exactly what the shared corpus exists for.

**Three bugs found by doing this, all silent, all fixed:**

1. **A hyphenated skill never matched its spaced spelling.** A profile saying `full-stack` matched
   nothing in a posting titled "Full Stack Developer" — the feature simply read 0. Found by diffing
   platform-derived tokens against the reference's hardcoded list on eight real postings; only 4 of
   8 agreed before the fix.
2. **Splitting a compound produced fragments that fire on unrelated text.** `Spring Boot` yielded
   the token `boot`, which matches *bootstrap* and *bootcamp* — one NOT-list entry becoming a gate
   on jobs it has nothing to do with.
3. **`remote_policy: "any"` dropped the location gate entirely.** Andras accepts on-site, hybrid and
   remote, but only in four countries. Workplace *mode* and acceptable *region* are independent, and
   conflating them would have shown him roles requiring relocation anywhere on earth.

Two operational lessons, both paid for: the embed backfill commits per batch now (a whole pass is
hours of work a dropped session throws away), and it sizes its own client timeout (the 120 s default
is for the harvester's small batches, not a 200-text backfill).

New scripts: `set_profile`, `migrate_reference_history`, `embed_backlog`, `train_user`,
`score_user`, `show_accounts`, `set_password`.

**What is left is credentials, not code.** Neither account has a login password or a Telegram bot
token — both are values only their owner can supply, via `set_password.py` (prompts, no echo) and
the credential step. Everything they gate is built and proven.

## 2026-07-27 (night, Phase 3 opens)

### Phase 3 step 1a — auth primitives

`libs/auth`. `AUTH_REUSE.md` option B: port the **design**, not the Go code. FlowSolutions'
`internal/auth` is ~3,900 lines against MongoDB, and lifting it would reintroduce a second language
and a second datastore against D1. The value is the fifteen decisions in §3 — each easy to get
subtly wrong, each already settled in production — so those are copied verbatim while the primitives
come from maintained libraries.

- **argon2id** at the production parameters (`time=1, memory=64 MiB, threads=4, keylen=64,
  salt=32`).
- **Tokens stored as SHA-256 hashes only.** The plaintext exists in the email and nowhere else, so a
  dump does not let anyone verify an address or reset a password. Reissuing revokes the previous
  link, and a verification token cannot be redeemed as a reset — that would turn "can receive email"
  into "can take the account" without the user asking for a reset.
- **Refresh sessions hashed at rest and rotated on every use.** A replayed *rotated* token revokes
  the whole family: either the client is buggy or the token was stolen, and in the second case both
  holders should end up with nothing rather than one silently keeping access.

**Two of `AUTH_REUSE.md` §4 honoured:** the 8-character password minimum is raised to 12 (this
platform holds live Claude keys and proxy credentials), and the v1 hashing path is not ported at
all. The breached-password check is left as a documented seam — adding a network call to signup
without a timeout and a fail-open decision would make registration depend on a third party being up.

**One bug CI caught:** `rotate_session` treated a *revoked* token as a replay, so logging out on a
laptop and then having the browser retry with the stale cookie revoked every other device. Closing a
tab should not log you out of your phone. Only a rotated token is a replay.

Migration `e5f1c0a7b284` adds `user_sessions` and `auth_tokens`. 451 tests.

## 2026-07-27 (night, later)

### Two accounts on one stack — the D6 comparison is live

`andraslekli@gmail.com` migrated from `andras-job-search` the same way the first account was: config
copied verbatim, nothing in the reference touched. 15 search terms, 5 locations.

Both accounts now drive one shared harvest:

```
active accounts: 2
  7533bb9c…  23 terms  [european_union, united_kingdom, budapest, europe]
  d620f6f3…  15 terms  [united_kingdom, ireland, european_union, budapest, europe]

folded query set: 334 distinct queries, 0 wanted by more than one account
budget: 60 planned per cycle, 274 deferred
  each account: exactly 30 of the 60 planned
```

**The 30/30 split is round-robin fairness working in production**, not a coincidence — a FIFO queue
would have given the first account all 60 and the second nothing until its terms aged in.

**Zero shared queries, and that is the honest result.** A software engineer and a mechanical
engineer have no search terms in common, so the query fold saves nothing here. The saving that does
apply is the corpus: one posting is stored, deduplicated, and embedded once regardless of how many
accounts it reaches. 311 rows, 305 embedded, harvested on the 15-minute timer.

`Location.IRELAND` added — not the same as `DUBLIN`, for the reason `UNITED_KINGDOM` is not
`LONDON`: a country-level search returns roles the city one never sees.

Migrations `c41a9f77d3b2` and `d8b3e5a91c47` applied to the live database, which was still on
`b072fd4c1edb`.

**What Phase 2's exit criteria still need.** "Two accounts, each with their own model, feed, and
schedule" is two-thirds done: both accounts exist, both harvest, both are budgeted fairly. Neither
has a *model*, because a model needs labels, labels need a rubric, and a rubric needs a confirmed
profile — and a profile needs the user's own Claude credential to capture. Credential entry and
profile confirmation are Phase 3's first steps, so the criterion closes there rather than here.

Deliberately **not** done: fabricating profiles by parsing each reference instance's rubric prose
into structured fields. That is precisely the thing `PROFILE_CAPTURE_PLAN.md` §1 says cannot be done
reliably — a wrong NOT-list entry becomes a hard gate that silently suppresses good jobs — and doing
it to tick a box would put two real people on rubrics nobody confirmed.

## 2026-07-27 (night)

### Phase 2 steps 4-6 — queue, trainer, notifier, and the workers that run them

`libs/queue`, `services/trainer`, `services/notifier`. The last of Phase 2's components, plus the
wiring that makes them run rather than merely exist.

**Queue** (T5/T6/T7). `SKIP LOCKED` claim, attempts incremented at claim, per-lane visibility
timeouts swept back to `ready`, bounded attempts to a dead letter, and immediate termination for
conditions that will never succeed. Fairness is round-robin by last completion on `train` and
`label` only — `notify` and `docgen` are naturally spread, and paying for the subquery where it
changes nothing is just latency. The notify dispatcher advances `next_run_at` at **enqueue**, which
is the fix for the most likely silent failure on the platform: a feed that stops forever because one
delivery errored.

**Trainer.** Training set scoped by `profile_generation`; a mixture of embedder versions refused
outright; unembedded rows dropped rather than zero-filled. The artifact is written temp-then-renamed,
and the `models` row, the `is_active` flip, and the `model_version` bump are one transaction.

**Notifier.** `notified_at` marked in the transaction the caller commits with the job — that is what
makes at-least-once delivery safe. A revoked bot is terminal, not a retry. Over the daily cap items
are deferred, not dropped. The token is decrypted inside `SecretStore.use` and scrubbed from
anything logged in that window.

**Three bugs CI caught that local green could not**, all of them the kind that stays silent:

1. **`claim()` returned the identity-mapped copy of the row.** A long-lived worker session would
   read `attempts` and `state` as they were *before* its own claim, so a job that always kills its
   worker would retry forever. Fixed with `populate_existing`.
2. **A handler failure rolled back the claim along with the handler's work.** `attempts` went back to
   its pre-claim value, `fail()` never reached `MAX_ATTEMPTS`, and dead-lettering existed only on
   paper. The increment is now captured before the handler runs and re-applied when the failure is
   recorded.
3. **The corpus CHECK requires `text_sha256` alongside a vector** — a vector whose source text is
   unrecorded cannot be verified as current. Fourteen trainer tests failed on the constraint rather
   than on anything they were testing, which is the schema doing its job.

**Workers.** `run_worker` claims, runs, and commits once, so the handler's effect and the job's
completion land together or neither does. Handlers are given the session and must not commit. SIGTERM
stops at the next job boundary. `services/trainer` sets the BLAS thread caps before numpy is imported
— queue concurrency of 1 does not stop one fit saturating every core.

432 tests.

**Phase 2's exit criteria are not met, and will not be until Phase 3.** Every component is built and
tested, but "two accounts running side by side, each with their own model, feed, and schedule" needs
two real accounts — and an account needs credential entry and profile confirmation, both of which are
web-layer work. What exists now is the machinery underneath that, complete and wired.

## 2026-07-27 (evening, later)

### Phase 2 step 3 — the multi-tenant scorer

`services/scorer`: an LRU of per-user models that reloads on `users.model_version` instead of on a
container restart, plus the serve path.

- **Hot-swap is a version comparison on every score**, not a timer. A timer means a window where a
  just-retrained model is not yet serving and nothing says so. The check is one indexed read —
  deliberately cheaper than the reload it prevents.
- **Nothing is embedded at score time.** The corpus row already carries its vector, so N users over
  one posting is N predictions rather than N embeddings. That is the economic argument for the
  shared corpus, and it only holds if this path never falls back to embedding.
- **Four fail-closed refusals**, each of which is otherwise a silent wrong answer rather than a
  crash: no artifact row for the version the user is on (interrupted trainer — serving the previous
  version would answer with a model the dashboard calls superseded), a `profile_generation`
  mismatch (the feature columns still line up; the weights were fit against other semantics), a
  `FEATURE_SCHEMA_VERSION` mismatch, and a corpus vector from a different embedder. The embedder
  check is on the **version string before the dimension** — two models can share a width and mean
  entirely different things.
- The cached-then-invalidated case is the subtle one: a model that was current when cached, with the
  profile edited afterwards. `model_version` never moves, so only the generation check catches it.

24 tests, all against a live Postgres with a stub estimator — what is worth testing is which model
is served to whom and when it is refused, not whether scikit-learn multiplies correctly.

## 2026-07-27 (evening)

### Phase 2 step 2 — rubric render and the per-user feature extractor

`libs/prompts/label_template.md` (SACRED), `libs/scoring/rubric.py`, `libs/scoring/features.py`.
Both derived from the same confirmed profile, both landed together because they invalidate together.

- **The template is platform-owned; the profile fills two blocks.** Decision procedure, score
  anchors, calibration examples, and output contract stay identical for every user — otherwise a
  template change could not be A/B measured across the platform. Calibration examples are phrased
  *relative to the candidate* ("a role in their FIRST target area, 2+ core overlaps → 93"), which is
  the option `PROFILE_CAPTURE_PLAN.md` §6.1 preferred because it keeps rendering deterministic.
- **`profile_version = sha256(rubric_md)[:8]`**, and `rubric_relevant_digest` hashes only the fields
  the rubric actually reads — so editing a phone number does not relabel a user's whole history (§7).
- **`save_profile` renders and stores the rubric itself.** A user with a profile and no rubric, or a
  rubric whose generation does not match, is a scoring corruption nothing reports. One write path.
- **The feature extractor is the third personal artifact** (§6.3). The reference's `_CORE_TOKENS`
  and off-stack list are one candidate's stack; here they are built from `users.profile`.
  `FEATURE_NAMES` order stays global and append-only — if the column order varied per user, no model
  artifact would be portable.

**Three real bugs the tests caught, all silent:**

1. `.NET` extracted as `net`, which then failed to match ".NET" at all — the leading dot sits inside
   the boundary lookbehind. A whole category of stack invisible to the model.
2. `\bc#\b` can never match: `\b` after `#` requires a following word character. Lookarounds over
   the actual token alphabet instead.
3. A term appearing in both the strengths and the NOT-list. The NOT-list now wins — a stale strength
   entry must not quietly cancel a gate the user reviewed.

Also fixed: `remote_policy: "any"` was rendering "mandatory relocation is a dealbreaker", inventing
a gate the user never asked for.

Region locks are per user too: the reference hardcoded "US-only is a gate" because its one user
could not work there. A user with US work rights no longer gets the flag.

32 tests. Migration `d8b3e5a91c47` adds `users.rubric_md` (separate revision — `c41a9f77d3b2` has
already been applied, and a migration is a record of what ran, not a file to revise).

**Gap:** `"1-3 years"` reads as 1, not 3 — the regex takes the lower bound. Carried from the
reference deliberately and now documented in a test, because changing it would shift the meaning of
a column every existing model was fit against.

## 2026-07-27 (later still)

### Phase 2 step 1 — profile capture, with manual entry in the same commit

`services/capture` + `libs/contracts/profile.py`. The Managed Agents interview that turns a signup
into a tenant, and the fallback for when it does not.

- **One narrow protocol** (`CaptureClient`, eight methods) with the SDK confined to
  `AnthropicCaptureClient`. Managed Agents is beta and sits on the onboarding critical path; a
  contract change should break one adapter, not onboarding. Tests drive the protocol with a fake.
- **Containment is configuration** (`PROFILE_CAPTURE_PLAN.md` §3): deny-by-default toolset (`bash`,
  `edit`, `web_search`, `web_fetch` off), no egress at all, read-only document mounts, and `write`
  approval-gated so the backend rejects any path outside the outputs directory. An uploaded CV is
  untrusted text; none of this is left to the prompt.
- **`users.profile` refuses an unconfirmed profile.** The confirmation click is the injection
  boundary, so it is enforced at the write rather than by convention. Saving bumps
  `profile_generation`, which is what makes everything built against an older one stale by
  definition (§6.4).

**Two subtleties that would have silently broken stream-first ordering:**

1. `stream_events` is **not** a generator in the adapter. A generator body does not run until the
   first `next()`, so `stream_events(...)` then `send_event(kickoff)` would have opened the SSE
   connection *after* the kickoff — undoing the ordering the caller carefully arranged. It opens
   eagerly and iterates lazily.
2. The session is created **without** `initial_events`, deviating from the plan's snippet. A session
   created with them starts running immediately, which has the same effect.

34 tests, no network. Migration `c41a9f77d3b2` adds `users.profile`, `profile_schema_version`,
`capture_agent_id`.

**Gaps:** the confirmation *form* is Phase 3 (no web layer yet) — `save_profile` enforces the
boundary the form will satisfy. Capture cost is still unmeasured, and it is the first money a user
spends, on their own key.

## 2026-07-27 (later)

### Phase 2 step 0 — `libs/secrets`: envelope encryption, and T4 in the same commit

Phase 2's first commit, and **not** the order `ROADMAP.md` lists. The roadmap puts the secrets
module at step 6, after profile capture — but `PROFILE_CAPTURE_PLAN.md` §4.1 says capture runs on
the user's own Claude credential, and `TENANT_SAFETY.md` §5's onboarding order is
`credentials → capture`. Building capture first would mean holding a stranger's live API key with
no encryption behind it, which is exactly what §2 exists to prevent. Secrets first. It also unblocks
the proxy credential, which the LinkedIn loop already needs and currently has nowhere real to live.

**The design, three files:**

- `keyring.py` — AES-256-GCM. KEK from `SECRETS_KEK`, per-user DEK wrapped by it, secret sealed by
  the DEK. Two decisions worth naming:
  - **Owner and kind are bound in as associated data.** Without it, an attacker with database
    *write* access (not key access) can copy user A's ciphertext onto user B's row, or relabel a
    proxy password as a Claude key, and both decrypt silently. With it, both fail closed.
  - **A wrapped DEK carries the id of the KEK that wrapped it.** Two bytes now; unrecoverable
    later. Rotation becomes re-wrapping one small column per user — no secret is re-encrypted —
    and a retired key keeps reading old rows until `users_needing_rewrap()` comes back empty.
- `store.py` — the database layer. `user_id` required on every method, which here is load-bearing
  twice: it is also the AAD. `put()` overwrites rather than versions, because versioning keeps a
  revoked credential decryptable. `purge()` deletes the rows **and** clears the DEK — crypto-
  shredding, so "destroyed on account deletion" is true across the backups too, not just live.
- `redaction.py` — two layers, because neither is enough alone. A `Secret` type whose `str`,
  `repr`, and f-string all render `<secret:claude_api …ab12>`, so the accidental-interpolation path
  is closed *at the type level*; plus a logging filter as the backstop for values that reach a log
  through code we do not own.

**Two things that would have been silently wrong:**

1. **A redaction filter on the root logger protects almost nothing.** A logger's filters run only
   for records logged *on that logger* — a record from `logging.getLogger("services.notifier")`
   propagates straight to the root's *handlers* without consulting the root's filters. So `install()`
   attaches to the logger **and to each of its handlers**, and a test logs from a child logger
   specifically to prove it.
2. **Tracebacks are formatted after filters run.** Scrubbing `record.msg` alone leaves the
   credential in the traceback — which is precisely where a failed proxied fetch puts it, since the
   connection error quotes the URL it dialled, userinfo and all. The filter renders and redacts the
   traceback itself into `record.exc_text`, which `logging.Formatter` then uses instead of
   re-rendering.

**T4, same commit** (`tests/test_secrets.py`, 42 tests). The headline assertion is made the way a
thief would experience it: dump every column of every row belonging to a user and search the lot for
the plaintext — not "the ciphertext column differs", *nothing*. Plus: a different KEK cannot read
what this one wrote; no rendering of a `Secret` shows the value; a known secret cannot appear in a
log line, a child logger's line, an object argument, or a traceback; and the API read path exposes
`last_4` and timestamps with no field that could ever carry the value.

Test values are shaped like the real thing (`sk-ant-…`, a `\d+:…` bot token, a proxy URL with
userinfo) because the pattern half of the filter recognises credential shapes — a `hunter2`
placeholder would pass tests the real value fails.

**Gaps recorded, not closed:**

- **Save-time validation is a seam, not an implementation.** `TENANT_SAFETY.md` §2 wants a real
  minimal API call before a credential is accepted. `put(..., validated=False)` and
  `mark_validated()` exist; the callers that make the call arrive with capture (Claude) and the
  notifier (Telegram). This layer deliberately does no network I/O, so saving a credential cannot
  be blocked by a third party being down.
- **Nothing calls `install()` yet.** The harvester's entrypoint gains it when the LinkedIn loop
  starts decrypting proxy credentials; until then no service holds a plaintext.
- **`SECRETS_KEK` is not yet set on the platform box.** Documented in `deploy/.env.example`,
  including why the first rotation is a config change rather than a migration.

317 tests. `SECRETS_KEK` and `cryptography>=44.0` added to the deploy env and dev requirements.

## 2026-07-27

### Phase 1 exit — the harvester runs against live sources

The cycle deployed and run on the platform box. **193 links, 185 new, 179 embedded, 0 source
failures**, and a second cycle immediately after found **193 links, 193 duplicates, 0 new** — dedup
holds across runs.

Corpus state after: 193 rows, 187 with text, **187 embedded**, 5 sources contributing. Vectors are
exactly 3,072 bytes (768 x float32) carrying `BAAI/bge-base-en-v1.5|dim=768|norm=1` — the 36-char
version string that would not fit the original `String(32)`.

**Three bugs, all found by the first contact with real data.** Every one was silent.

1. **Three sources made no HTTP request at all.** `remotive`, `himalayas` and `arbeitnow` iterate
   `cfg["queries"]` and return `[]` when it is absent. No request, no log, and the cycle reported
   success — because an adapter returning `[]` is the registry's contract. The same property that
   stops one dead source sinking a run also hides a misconfigured one.
2. **The recency window was 24h, not 72h.** The adapters default internally to 24 while the config
   schema's own default is 72, so the first cycle fetched a much narrower slice than intended — 8
   links where there should have been ~190.
3. **`content_key` was `String(64)`.** It is a readable composite (`company|title|location`), not a
   hash, so its length is a function of a job title somebody else wrote. A German posting produced
   85 characters and every ingest died. Third instance of the same mistake this session, after
   `embedding_version` and `models.embedder_model` — bounding a string whose length is not ours to
   bound. Every remaining `String(n)` column was audited; the rest are hashes, enums, or
   RFC-bounded.

**A fourth bug hid the third.** The advisory-lock release ran in a `finally` on an already-aborted
transaction, raised `InFailedSqlTransaction`, and propagated *instead of* the `DataError` that
caused it — so the log showed a lock problem and hid a schema one. Cleanup must never mask the
failure that caused it.

**Two sources return zero, and that is correct.** `weworkremotely`'s feed has 25 items whose newest
is 26 days old; `remotive` returns 36 jobs, all outside the window. The 72-hour filter is doing its
job. Recorded so nobody "fixes" it into admitting stale postings.

275 tests passing. **Phase 1 is functionally complete.**

### The harvest cycle - one-shot, locked, timer-driven

`services/harvester/cycle.py` and `__main__.py`. The last Phase 1 piece: the loop that runs the
components in order - demand -> budget -> sources -> dedup -> upsert -> embed.

**A one-shot command rather than a daemon with a sleep**, which was the open question. Four reasons,
in order of weight:

1. **Restart safety comes free.** A crashed one-shot is a run that did not happen and the next tick
   starts clean. A long-lived loop that dies stops harvesting *silently* until somebody notices -
   the failure class `OPERATIONS_PLAN.md` §4 exists to prevent.
2. **It composes with Phase 2 instead of being replaced.** `run_cycle` becomes the body of a queue
   worker; an internal timer would be thrown away.
3. **It is testable.** `TEST_STRATEGY.md` §2.1 forbids `sleep` in tests, which rules out testing a
   loop containing one.
4. **Each run is its own log line and its own exit code.**

The cost is overlap: a timer can fire while the previous run is still going. Handled with a Postgres
advisory lock **inside the cycle** rather than in systemd, so it behaves identically when Phase 2
moves this into the queue. `pg_try_advisory_lock`, not the blocking form - a tick that arrives late
should skip, not queue behind its predecessor and turn one slow cycle into a backlog all doing the
same work while each spends the full fan-out budget.

**A skipped tick exits 0.** With a 15-minute timer and an occasionally slower cycle, treating
overlap as failure would page somebody about the system working correctly.

`Persistent=false` on the timer, deliberately: catching up missed runs after downtime would fire
several cycles back to back against sources that are already grey-area. A missed harvest is simply a
harvest that did not happen.

**Universal cadence, and that is not a simplification.** There is no such thing as harvesting *for*
one user when the corpus is shared - one fetch serves everyone, so a per-user harvest interval would
mean N cycles fetching identical bytes, which is the design this architecture rejected. What users
choose is how often they are *told* (`notify_interval_min`) and how often their own paid LinkedIn
tier runs (`linkedin_interval_min`). Universal where the cost is shared, per-user where it is
personal.

Fails closed on a missing `EMBEDDER_URL` (exit 2) rather than harvesting rows nothing can embed -
they would sit unembedded and unscoreable while the cycle reported success.

265 tests passing.

### G11 and G12 closed, and a hole found in the drift check

**G11 (account lifecycle) was brought forward out of Phase 3**, because its stated cost is a Phase 1
one: "dormant users consume harvest budget and run their own LinkedIn tier against their own proxy
indefinitely". The harvest fold being built next is exactly what has to exclude them.

**Implemented against the product rather than against the sentence.** G11 defines dormant as "no
login in N days". Delivery is Telegram — a user who reads their feed every morning and applies to
things may never open the web app again after onboarding, so login-only would pause the feeds of the
most engaged users and tell nobody. `last_active_at` is bumped by login **or** any Telegram action.
30 days, reversible instantly, and `suspended` is deliberately not reversed by activity: dormancy is
a cost control, suspension is a decision.

**G12 (artifact growth)** — `prune_model_artifacts` keeps current plus one previous. It returns the
paths rather than deleting files: the repository owns rows, not the filesystem, and splitting it
means a rolled-back transaction cannot leave rows and files disagreeing about which versions exist.

**The find that matters: Alembic autogenerate does not detect `CHECK` constraint changes, and
neither does the CI drift check, because it is built on autogenerate.** Adding `dormant` to
`USER_STATUSES` produced a migration that added the column, left the constraint listing the old four
values, and passed the drift check — while every write of the new status failed with an integrity
error. Recorded in `libs/db/README.md` alongside the other silent failure mode (a model not imported
by `models/__init__.py`), because neither is discoverable from a green build.

A second, smaller trap in the same fix: `op.drop_constraint` takes the **logical** name
(`status_valid`), not the rendered one — the naming convention expands it, so passing the rendered
name produces `ck_users_ck_users_status_valid`.

**G9 (cost visibility) stays open** and is genuinely Phase 3: it meters Claude spend, and nothing
calls Claude yet. The one part worth taking early — integer units rather than floats for anything
billed — is already the precedent (`proxy_credentials.mb_used_today` is `Numeric`, not `Float`).

255 tests passing.

### Step 5 - embed once, for everyone, ever (T8)

`libs/contracts/embedder.py` and `services/harvester/embedding.py`. A vector is a deterministic
function of the text, so embedding the same posting for fifty users is fifty times the CPU for
byte-identical output. Putting it once on the shared corpus row is the whole scaling argument.

**A regression here is invisible until the CPU bill** - nothing errors, feeds populate, scores look
reasonable. So T8 asserts the backend call count through a counting stub rather than trusting the
structure.

**A real schema bug, and it would have failed on the very first production embed.**
`corpus.embedding_version` was `String(32)`. The live embedder's actual version string is
`BAAI/bge-base-en-v1.5|dim=768|norm=1` - **36 characters**. Every embed would have failed with a
string-truncation error. It surfaced only because the test used the real format rather than a short
placeholder, which is an argument for fixtures that look like production. `models.embedder_version`
had the same bound and the same bug. Both are `Text` now: the version is a composite of everything
defining the vector space, and its length is a function of the model name, which is not ours to
bound. Migration `6f4ca35c9cb4`.

Three properties the tests pin, each silent if lost:

- **Compatibility is asserted before anything is written.** A vector from the wrong model is not a
  worse vector, it is a meaningless one, and nothing downstream catches it - the dimensions match
  and the numbers look like numbers.
- **The digest is re-checked at write time.** A cold-start batch takes minutes while `refresh.py`
  mutates descriptions; a vector whose `text_sha256` no longer matches is refused and the row is
  picked up next pass.
- **The bulk lane is used, not the fast lane.** They are separate worker pools, not a priority flag,
  because a score request otherwise waits behind an in-flight bulk call - measured at 0.08s idle
  versus 8-15s with a shared pool.

Also refused rather than guessed: a short vector list (fewer vectors than texts means the mapping is
unknown, and guessing attaches one job's vector to another job's text), and a gateway 503.

247 tests passing. **Phase 1 is complete.**

### Step 4c - the source adapters, ported as a scripted transformation

~3,500 lines lifted from `pipeline/sources/`, plus `libs/corpus/filters/timefilter.py`. Done by
`services/harvester/sources/port_from_reference.py`, which is committed alongside them: the
transformation is the interesting part of the port, and re-deriving it later from a diff would be
far harder than reading it. Retyping the adapters by hand would have introduced transcription errors
into code whose correctness depends on details no test here can check - a JSON path, a date format,
a pagination parameter.

**4c was not a pure lift, and the reason matters.** Every adapter built its own `httpx.Client` - 17
call sites. Harmless single-tenant, because none of those boards need a proxy. Here it would have
broken the invariant `fetch.py` exists to hold **and** restored `trust_env=True` on all 17, so an
ambient `HTTP_PROXY` could route the whole shared harvest through one user's credential. There is
now a test asserting no module under `services/harvester` constructs a client directly.

**Two real bugs found during the port:**

1. **The transformation silently dropped RemoteOK's custom User-Agent.** The reference sets one with
   the comment "RemoteOK blocks the generic Mozilla UA; a slightly more specific one works" - and
   my regex matched `headers=ua` and discarded it. The source would have returned nothing, with the
   explanation sitting two lines above. Caught by ruff flagging the now-unused variable, which is
   luck rather than design, so the rule is narrow now: only `headers=_UA`, the platform default the
   factory already supplies, may be dropped.
2. **`gather_linkedin` took its proxy from a config dict.** Correct with one tenant, wrong with
   many: a credential arriving as a dictionary key has no verified owner. It now requires a
   `ProxyLease`, keyword-only and mandatory. jobspy opens its own connections and cannot be routed
   through `make_client`, so this is the one place a proxy URL reaches a third-party library - which
   is exactly why the type carries its owner.

**One deliberate behaviour change: XML parsing now uses `defusedxml`.** Several adapters parse
RSS/XML from third-party boards with stdlib ElementTree, which the stdlib's own security docs flag
as vulnerable to entity-expansion bombs. Single-tenant on a laptop that is a shrug; here the
harvester shares a box with Postgres, and a hostile or merely broken feed exhausting memory takes
the platform down for everyone.

The registry now records **which tier a source belongs to**, so the shared loop cannot be handed the
proxied one - the same guarantee `make_shared_client()` gives at the HTTP layer, stated twice
because it is what D3 rests on. LinkedIn is the only per-user source, and a test asserts it.

Adapter style findings are ignored per-file with reasons, as for the embedder: restyling lifted code
would destroy the diffability that justifies a scripted port. **F841 stays on** - it is what caught
the User-Agent regression.

233 tests passing.

### Step 4b - the fetch layer, where D3 stops being a policy and becomes structure

`services/harvester/fetch.py` and `proxy_usage.py`. Decision D3 says a user's proxy credential is
used strictly for their own fetches, never pooled. That is a claim about *every* outbound request the
harvester makes, so it can only hold if there is exactly one way to make one - `make_client()` is
that way.

Three departures from the reference, all forced by multi-tenancy:

- **The proxy comes from a lease, never the environment.** The reference reads `PROXY_URL` from env,
  which is single-tenant by construction. There is no env fallback here on purpose.
- **A client carries the id of the user it was built for**, so "was this credential used for someone
  else's fetch" is an assertion rather than a code review.
- **`PROXY_HOSTS=*` is gone.** Under per-user billing, proxy-everything would spend one user's
  metered bandwidth on shared ATS fetches that work fine direct.

`make_shared_client()` takes no lease parameter at all, so the shared loop cannot spend anyone's
bandwidth even by mistake. Structural, not conventional.

**A real bug the tests caught: `trust_env`.** The shared client set no mounts, so httpx defaulted to
`trust_env=True` and would have honoured an ambient `HTTP_PROXY` - silently routing every user's
shared traffic through one endpoint. That is the pooling D3 forbids, arriving through an environment
variable nobody set deliberately, and it would have looked like it was working: jobs arrive, feeds
populate, and the only symptom is one person's proxy bill. Now `trust_env=False` everywhere, with a
test that sets the env vars and asserts they cannot leak in.

**A second bug: the daily window.** `roll_daily_window` treated a null `usage_reset_at` as "the day
has turned" and zeroed the counter, so the first lease of any credential handed back a spent cap.
Null is initialisation, not a day boundary. Fixed, and the timezone case is now tested in both
directions - a user east of UTC must reset on *their* midnight and must not reset on UTC's.

### T13 and T1 closed

**T13 is done, both halves.** The visibility half was already proven; the cross-use half - the more
important one - now is: owner identity on the client, LinkedIn-only routing, redirects out of
LinkedIn going direct, ambient proxy rejected, metering on every response including failures (the
proxy bills on the wire, not on success), and quarantine on *consecutive* failures rather than
cumulative, which would eventually quarantine every healthy user.

**T1 is complete for every read path that exists.** Added config, secrets metadata and queue rows to
the isolation coverage. Documents remain uncovered because `docgen` does not exist yet - that is
named in the status table rather than left as an unexplained amber.

Secrets have a metadata-only read path by design: `last_4` and timestamps, never ciphertext. There is
deliberately no repository method that returns a secret value; the worker that needs a plaintext
decrypts it in memory for one call and that path does not go through the repository.

213 tests passing.

### Audit against the specs, and T9

Checked what is built against `TEST_STRATEGY.md` and `GAPS_REVIEW.md` rather than against a feeling
that things are on track. Status is now recorded next to each spec — no fourth place tracking state.

**The audit found a real miss: T9 had no tests.** `libs/contracts/config.py` shipped in the previous
commit and the config schema was never tested, despite T9 scoring 7/10 as "primary onboarding
failure mode". `TEST_STRATEGY.md` §4's ordering rule exists precisely to stop that; it was followed
for T2 and T1 and not here. Now 34 tests, including the seam where a *validated* config actually
drives the harvester — testing the schema and the query-set builder separately would have left the
join between them unproven, and that join is where a renamed field silently produces zero queries.

**Writing that seam test found a second problem: the per-user keyword cap was defined twice** — a
literal in the schema and a constant in `services/harvester`. Two definitions of one rule is how
limits drift: raise one and the other silently accepts terms it will never serve. The first instinct
was to write a test asserting the two numbers agree; the right fix was to remove the duplication.
It moved to `libs/contracts`, which is the **only** package both may import from, since `libs` must
never import a service. At Phase 3 it becomes a row in `plans.limits` and the seam is unchanged.

Honest status carried into the docs:

- **T1 is partial, not done.** Scores, labels, applied, and cascade completeness are covered;
  config, secrets, queue, and documents are not, because those read paths do not exist yet. It is
  the release blocker and must be finished before launch.
- **T13 is half done.** The visibility half is proven — no healthy credential, no LinkedIn rows,
  per-user on one shared row. The "credential never cross-used" half needs the fetch layer, and is
  the more important half.
- **G5, G7, G14 closed. G15 and G16 are policy-and-schema built, execution deferred** to the queue
  (Phase 2) and step 5 respectively.

Anti-flake compliance re-checked against §2: no `sleep`, no real network, every database test in a
rolled-back transaction, seeded or injectable RNG wherever anything samples, no wall-clock
assertions, real Postgres 17 rather than SQLite.

Infrastructure: **4 GB swap is live on the VPS** (`swappiness=10`, persistent in fstab), which
closes the risk raised when TEI's CPU ceiling was raised on a box where memory contention was an
OOM-kill rather than a slowdown.

173 tests passing.

### Step 4a - the harvester's governor, before the harvester

Phase 1 step 4, split: the deterministic half now, the source adapters as 4b. Same fault line that
made step 3 tractable - build the part whose correctness is provable without a network first, and in
this case build the thing that *limits* scraping before the thing that scrapes.

- **`libs/contracts/config.py`** - the bounded user config. Invalid states are unrepresentable
  rather than validated away: controlled vocabularies, 1-5 keywords, bounded salary and recency.
  Locations are a picklist and that is not only a UI choice - harvest dedup folds on a normalised
  location, so free text would multiply the query set with spellings of the same place.
- **`services/harvester/query_set.py`** - QueryKey normalisation, folding, fan-out budget,
  round-robin selection.
- **`services/harvester/governor.py`** - the LinkedIn tier's global controls.
- **`proxy_credentials`** table, and the visibility gate in `libs/tenant`.

**A bug the tests caught in my own implementation.** `HARVESTER_PLAN.md` section 2 specifies the age
bucket as *"rounded up to the coarsest bucket any requesting user asked for"*, so a 24h user and a
72h user share one 72h fetch. I had bucketed each user's value independently and folded on it, which
*fragments* demand instead of merging it - two fetches whose results are strict subsets of one
another, exactly the duplicate work the budget exists to prevent. Age is now resolved per group
after folding.

Three more failures in that run were bugs in the tests, not the code: I had given every user
identical keywords, so folding correctly collapsed them and the scenarios I meant to build never
existed.

**Round-robin is the load-bearing choice in selection.** Ordering by demand would serve popular
terms repeatedly and starve anyone with an unusual search - precisely the user for whom this product
is most valuable. Deferred queries are served first next cycle so an unlucky query does not stay
unlucky.

**The visibility gate makes free-riding impossible by construction.** LinkedIn rows live in the
shared corpus so dedup and embed-once still work, but a user sees them only while their own proxy
credential is healthy. Applied as a `WHERE` clause, not a post-filter - filtering after `LIMIT`
would hand short pages to exactly the users who have no credential.

**The per-user tier still needs a global governor** (section 5.2b). Per-user proxies give IP
diversity and handle ban risk; they do nothing about the platform issuing hundreds of concurrent
requests, or a herd forming when users share an interval. Hence a concurrency ceiling, jitter
applied per scheduling decision (not once at signup, or a restart re-converges everyone), and a
kill switch - the alternative during an incident is editing 50 users' configs while LinkedIn blocks.

Execution is deliberately not wired: the per-user loop is an ordinary queue job, and `libs/queue` is
Phase 2. Nothing half-built runs.

### CI now lints

`ruff check .` added as a step, after noticing ruff had only ever been run by hand - which means it
had only ever run on the files someone remembered to name. Rules are selected explicitly rather than
inherited from ruff's defaults, which change between releases. `services/embedder` carries per-file
ignores with reasons: its findings are deliberate choices in deployed, gate-tested code, and one
flagged line (`bulk_s == bulk_s`) is an intentional NaN check that a "fix" would have broken.

Migration `2f362ab1e0c5`. 140 tests passing.

### Step 3b — the remaining stores, plus a dedup layer step 3 had missed

**The important part is the gap, not the port.** Reading the reference's `processed` table for 3b
showed it indexes `app_canonical` and matches on `canonical = ? OR app_canonical = ?` — the resolved
**Apply URL**. Step 3's `find_duplicate` only compared `corpus.canonical_url`, so a LinkedIn posting
whose Apply button resolves to a Greenhouse board would have landed as a **second row** alongside the
one the ATS poll found directly. That is the precise case canonicalisation exists for, and it was
missing.

Fixed: `corpus.application_url` + `application_canonical_url`, and a fourth dedup layer checked in
**both directions** — the candidate's apply target against stored rows' own URLs, and the
candidate's own URL against stored rows' apply targets. Checking one direction only would leave half
the collisions undetected while looking like it worked.

The stores themselves:

| Reference | Here |
|---|---|
| `ats_tokens.json` | `ats_tokens` table, lowercased, dead boards retained not deleted |
| `ats_etags.json` + `.lock` | `ats_etags` table, **row-level upsert, no lock** |
| `applied.jsonl` | `user_job_state.applied_at` / `.cover_built` |
| `labels.jsonl` | `user_labels`, `source='llm'` |
| `feedback.jsonl` | `user_labels`, `source='feedback'`, `weight=5` |

**`ats_etags` is the one place the port is a fix rather than a translation.** The reference guards a
whole-map JSON rewrite with a file spinlock whose `_acquire_lock()` returns `False` on timeout and
lets the caller proceed anyway — so a contended write silently drops another writer's keys, and even
holding the lock the whole-map replace lets two ATS gathers clobber each other. Row-level upsert on
`(ats, token)` removes both by construction. Proven by an interleaved-write test, not asserted.

**Feedback turned out not to be a separate store** — it is a label with a different provenance and
weight. The reference keeps it apart and blends it at train time by duplicating rows five times so
the weighting survives sklearn Pipelines; recording `source` and `weight` on the row makes the blend
a query instead.

New package **`libs/tenant/`**, a deliberate addition to `CODE_STRUCTURE.md` §2's list. That list
predates the repository layer and has no home for per-user data access: `libs/corpus` documents
itself as the shared half, `libs/db` is where the schema lives. Putting tenant-scoped queries in
either would make the shared/per-user boundary invisible at the call site. Now it is visible in the
import path — `libs.corpus` takes no `user_id` at all, `libs.tenant` cannot be used without one.

**T1 landed** (`TEST_STRATEGY.md`, release blocker): two users, overlapping jobs, every read path
returns only the caller's rows — applied log, labels, and scores.

Also caught in review: `cover_built` was generated as `NOT NULL` with no server default, which fails
against any table that already has rows. It would have worked today only because the table is empty,
which is not a migration strategy. Added `server_default`.

Migration `fccd4d3a84ff`, applied to the live database (11 tables). 109 tests passing.

### Corpus repository — the storage half of the `store.py` port (Phase 1 step 3)

`libs/corpus/repository.py`, 14 behaviour tests, T2 green throughout. Dedup (url → canonical →
content_key, narrowing), ingest, description/embedding lifecycle, and the retention sweep.

**A schema correction the port surfaced.** The first draft made `corpus.canonical_url` unique and
`NOT NULL`. Both wrong, and only writing the repository exposed it:

- The reference keys `all_links` on **`url`** — `canonical` and `content_key` are *indexed, not
  unique*. Dedup is a query performed **before** insert, not a constraint. Making canonical unique
  looks tighter but converts a case the reference tolerates into a crash mid-harvest.
- Both derived keys must be **nullable**, because the key functions return `None` for input they
  cannot use. `NOT NULL` would make such a job impossible to store at all — silently dropping real
  postings rather than admitting them undeduplicated, which is the wrong direction to fail.

Migration `fe23e709fd46`, applied to the live database.

Two invariants the tests pin, both silent if broken: `first_seen_at` is never overwritten on
re-discovery (or a re-listed posting stays permanently fresh and un-trimmable), and a vector can
never outlive its text — changing a description clears the embedding, and `set_embedding()` refuses
a vector whose digest no longer matches. That last one is a real race, since bulk embedding takes
minutes while `refresh.py` runs concurrently, and the `(model, dim, version)` assert cannot catch it:
the vector space is right, the content is wrong.

Decided while building it: `CorpusRepository` takes no `user_id` at all (shared by construction,
per-user repositories are separate classes), and the reference's materialised `.load()` mode is
dropped — it was an artifact of the JSON store that preceded SQLite, and would mean an O(history)
memory pattern in a multi-tenant service.

### CI — three bugs that made the pipeline report success without checking

Verified every job by running it the way the workflow does, against a scratch database. Two of the
three made a check pass while checking nothing:

1. **pytest could not import `libs/` in CI.** The workflow calls the `pytest` console script; only
   `python -m pytest` puts the working directory on `sys.path`. Every test importing `libs.*` would
   have failed at collection in CI while passing locally — the worst split, because local green is
   exactly what gets checked before pushing. Fixed with `pythonpath = ["."]`.
2. **The drift check could never fail.** It globbed `*drift-check*.py`, which Alembic never writes —
   `file_template` slugifies the hyphen to `drift_check`. grep errored, the `if` read false, pass.
3. **The first rewrite still passed when Alembic itself failed** — a failed `generated=$(ls …)`
   assignment did not abort under `set -e`.

Proved the fix rather than assuming it: injected a canary column into the corpus model, confirmed
the check fails, reverted, confirmed green. A drift check that cannot fail is worse than none,
because it is also reassuring.

### T2 — the dedup regression suite, and the pure-function half of the `store.py` port

Phase 1 step 2. `libs/corpus/keys.py` is a faithful port of `canonical_url` and `content_key` from
`job-search/pipeline/lib/store.py`, with **66 tests** proving it.

**The port was split in two, and that is the whole reason T2 could come first.** The dedup
*semantics* are pure functions; the SQLite→Postgres move is *storage*. Separating them means T2 was
written and green before any storage work started — which is what the roadmap asks for and would
have been impossible had the two been ported together.

The proof has three layers:

1. **Golden cases** generated by *executing* the reference, not by reading its rules and writing
   down what they appear to say. A misreading transcribed into a test is confirmed by that test
   forever. Regeneration: `tests/golden/generate_dedup_golden.py`.
2. **Behavioural tests** restating each rule as intent, so the suite explains *why*.
3. **Differential fuzz** — 40,000 seeded random inputs through both implementations, **0
   divergences**. Skips where the reference repo is absent (including CI), which is why the goldens
   are committed rather than generated at test time.

**Found: the reference's own comment is wrong.** It claims `"Remote" == "Remote - EMEA" == ""`. It
does not — `remote` is stripped from the location but `EMEA` survives, so the two key differently
and do **not** collapse. Pinned by a test named after the discrepancy, because otherwise someone
eventually "fixes" the code to match the comment and silently changes what counts as a duplicate.

### Embedder deployed on the platform box

`services/embedder` now runs on `169.58.16.241` as the **platform's own instance**. The legacy
deployment serving `job-search` and `andras-job-search` lives on a different machine and was not
touched — decision D6 is parallel copies, decommission nothing until the platform provably matches.

Gate results, run in order per `services/embedder/README.md`:

| Gate | Result |
|---|---|
| 1 — backend-verified health | ✅ `verified: true` in ~45 s, `model=BAAI/bge-base-en-v1.5 dim=768` |
| 2 — full suite against the live stack | ✅ **23 passed, 1 skipped** (up from 15/9 with no stack) |
| 5 — priority lane under a training flood | ✅ included in gate 2 |
| 4 — peak RSS vs the 2 GB budget | ✅ **832 MB peak** against 2048 MB, 0 errors |

Gate 4 also showed `cpus: 1.5` on the TEI container was the binding constraint — a ceiling tuned for
the box that shared CPU with two single-tenant stacks, leaving ~6.5 of 8 vCPU idle here. Raised to
**4** and re-measured rather than assumed:

| | cpus 1.5 | cpus 4 | |
|---|---|---|---|
| Bulk cold start (2,000 texts) | 589.1 s | **296.7 s** | 1.99× faster |
| Score latency, median | 3.795 s | **1.586 s** | 2.39× faster |
| p95 | 4.862 s | **2.603 s** | 1.87× |
| max | 9.101 s | **5.158 s** | 1.76× |
| Peak RSS | 832 MB | **810 MB** | ↓ |
| Errors | 0 | 0 | |

**2.67× the CPU bought 2× the throughput.** Sub-linear, because TEI's CPU backend caps its internal
batch at 8 regardless of how many cores it has. Extrapolating, 4 → 6 would likely return under 1.3×
for 50% more contention — so **4 is the landing spot and going further is not worth it**. That
conclusion is the reason to record the numbers rather than just the change.

Peak RSS went *down* slightly, which is consistent: more CPU means less queuing, so fewer texts are
resident at once.

⚠ **Re-measure once the other services compete for the box.** This number is tuned to a machine that
is currently almost empty — precisely the condition that stops being true.

The single skip is `test_parity` (TEI vectors vs in-process `fastembed`). **That gate is moot for the
platform** and was skipped deliberately: parity exists to decide *match-or-retrain* for classifiers
that already exist, and the platform has none — every user's model will be trained on TEI vectors
from day one. It still matters for the legacy box, which is why the test stays in the suite.

Tests were run in a throwaway `python:3.12-slim` container attached to `embedder-net` rather than a
host venv. That needs no `sudo` (`python3-venv` is not installed) and, more usefully, exercises the
path consumers actually use — the gateway by service name, not the loopback host port.

### Postgres schema — eight tables, Alembic-managed (Phase 1 step 1)

`libs/db`. Applied to the live database and verified: re-running autogenerate produces **0
operations**, so models and database agree exactly.

Three decisions that are not transcriptions of the design docs:

- **Added `jobs.finished_at`.** `SCHEDULER_PLAN.md` §3's DDL omits it, but §5's fairness ordering
  selects `MAX(finished_at)` per user per lane. Round-robin does not work without it.
- **`job_id` foreign keys are `RESTRICT`, not `CASCADE`.** Written as CASCADE first, then corrected:
  cascading there would let a corpus retention sweep silently delete `applied_at`, which §4 calls
  the record a user would be angriest to lose. `user_id` still cascades, so account deletion stays
  one statement.
- **Config split by access pattern.** The four fields the 60-second dispatcher reads (`active`,
  `next_run_at`, `notify_interval_min`, `timezone`) are indexed columns on `users`; everything else
  the config form collects is JSONB on `user_config`.

Closes **G5** (Alembic from the first table, no hand-applied DDL ever) and **G7** (every timestamp is
`TIMESTAMPTZ`, so per-day counters can reset at the user's local midnight rather than UTC's).

`tests/test_schema.py` is the seed of **T1** — written now because the roadmap puts the isolation
test at the moment the first per-user table exists, not once there is a repository layer to test
through. CI runs it against a real Postgres 17 service, plus a drift check that fails if a model
changed without a migration.

### Postgres deployed

`deploy/compose.yml`. Loopback-only port, `${POSTGRES_PASSWORD:?}` so the stack refuses to start
without a password rather than defaulting to a guessable one, `mem_limit: 4g` because the box runs
no swap, `shm_size: 1g` because the 64 MB default makes parallel query workers fail with errors that
read like disk problems.

### Legal review cleared

GDPR posture, scraping ToS, and Anthropic's terms on subscription credentials in a hosted service
all confirmed cleared. The **implementation** obligations they imply are scheduled into Phase 3 —
see `docs/saas/ROADMAP.md`. A review passing and the obligations being built are different things.

## 2026-07-26

### Step 0b — capacity measured, D4 confirmed

23 GiB RAM with 22 GiB available, 8 vCPU, 382 GB free disk, ~337 MiB resident. Roughly 3× headroom
against the projection. Recorded in `PLATFORM_ARCHITECTURE.md` §11 along with the caveat that
matters more than the verdict: **neither single-tenant stack was on the box**, so the ~3.8 GB that
made the D6 arithmetic tight has not been paid yet.

Surfaced three follow-ups: the embedder needed standing up here (done 2026-07-27), the box has **no
swap**, and Postgres was not installed (done 2026-07-27).

### `embedder/` → `services/embedder/`

Its own commit, so a bisect lands on it cleanly. **15 passed / 9 skipped before and after,
identical** — the proof the move was inert.

Nothing relative needed editing: the compose build context, the Dockerfile `COPY` paths, and
`conftest.py`'s `sys.path` insert are all computed relative to files that moved together, and the
compose project name is pinned. Only things hardcoding a path from the repo root changed.
`CODE_STRUCTURE.md` §6 now records which surfaces those actually were, because the pre-move guess
was wrong in the direction that matters.

### Step 0a — repo layout and boundary enforcement

`libs/` and `services/` per `CODE_STRUCTURE.md` §2, as empty packages whose `__init__` docstrings
record why each exists. `import-linter` wired into CI on the first commit: **services may not import
each other**, **libs may not import services**. Every service is named in the contract from the
start, including still-empty ones, so a new service is covered the moment it has code rather than
whenever someone remembers to add it.

Boundaries are trivial to establish now and painful to retrofit once eight services import each
other. A rule a linter checks survives a late night; a rule in a document is a suggestion.

### Platform design

`docs/saas/` — twelve documents settling the shared/per-user split, container topology, storage
sizing, scheduler, tenant safety, onboarding, and operations. Start at
`docs/saas/PLATFORM_ARCHITECTURE.md`.
