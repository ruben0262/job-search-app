# Working rules for this repository

Standing instructions, set by the owner. They apply to every change, not to a particular feature.

## 1. Simple beats clever, every time

**The failure this prevents is the one this project keeps hitting.** Nearly every bug found in the
last week was not a wrong algorithm — it was a value written in one place and read in another, a
setting with no consumer, a filter nobody could see running. Complexity is what let those hide.

- One mechanism per job. If two pieces of code answer the same question, they will disagree, and the
  copy that is enforced will not be the copy anyone can see. This has cost six hours of ingestion,
  a permanently silent account, and a labeller that never started.
- Prefer a `WHERE` clause to a cache, a column to a service, a constant to a config system —
  until measurement says otherwise.
- No hardcoded values that an operator will one day need to change. Those belong in platform
  settings with a control in the admin panel.
- If an explanation needs a diagram, the design is probably wrong.

## 2. Behaviour gets a test, at the time it is written

Not "later", and not a test of how the code is arranged.

- **Test what the system does, never how it does it.** A test that asserts an internal shape passes
  while the feature is broken — which is exactly how eleven sources reached zero ingestion with a
  green suite.
- **The dangerous failures here are silent**: an empty list, a filter that rejects everything, a
  setting nobody reads, a query in the wrong shape. None of them raise. So assert the *presence* of
  results, not just the absence of errors.
- A new test must fail against the code that made it necessary. If it passes both before and after,
  it is testing nothing.

## 3. Delete the thing you replaced

- When a mechanism is reworked, the old one goes in the same commit. Dead code is not neutral: it
  gets read as current, imported by accident, and it hides which path is live.
- A setting, timer, column or list that nothing consumes is dead code with a friendly face. Find its
  consumer or remove it. Several have now been found by accident rather than by looking.
- Removing something is a decision that belongs in the commit message with its evidence, so it can
  be reversed by a person who was not there.

## 3a. Prompts are sacred. Never edit one without being asked

**No prompt changes without explicit confirmation from the owner, ever — not as part of a
refactor, not to save tokens, not to fix a bug the prompt appears to cause.** Propose the change,
state what it would buy, and wait. This is stricter than every other rule in this file: the answer
to "may I just reword this line" is always to ask.

What counts as a prompt:

- `libs/prompts/` — the generation prompts and `label_template.md`. The template's *hash* is every
  user's `profile_version`, so a wording change silently invalidates every label and every model on
  the platform at once.
- `libs/agents/*.md` — the setup commands. Byte-identical to the reference instance by design.
- `libs/scaffolds/` — what a new account is seeded with.
- **Every per-user file**: `base_cv.html`, `tailor_cv.md`, `tailor_cover.md`, `label.md`,
  `08-role-angles.md`, `context.md` and the rest of `user_profile_files`. These are not templates
  with a shared owner — each one is *that person's*, tuned for the quality of their CV and cover
  letter, and editing one is editing their work.

Two consequences worth stating, because they are what makes this rule expensive to break:

- A prompt edit is not local. It changes what the model writes for a real person applying for a
  real job, and the damage shows up as a worse CV rather than as an error.
- Where a change is genuinely wanted, it is change-controlled: approved explicitly, measured with an
  A/B, and recorded in `CHANGELOG.md` — the rule the reference instance already applied to
  `libs/prompts` (`CODE_STRUCTURE.md` §7.4), extended here to every file above.

If a prompt looks like the cause of a defect, say so and propose the wording. Do not apply it.

## 4. Measure before changing, and after

The roadmap's own rule, generalised: no optimisation, filter or removal ships without the number it
was justified by. "This will be faster" and "this cannot lose anything" are claims, and both have
been wrong here.

## 5. Raise hardcoded and unstable implementations, unprompted

**Do not wait to be asked.** When a change touches a constant that governs behaviour an operator or
a user would reasonably want to control, say so in the response — even if the task was something
else, and even if leaving it alone is the right call for now.

What counts:

- **A number in the code that decides how the product behaves.** Ceilings, windows, intervals,
  batch sizes, retry counts. If somebody might one day need it different, it belongs in platform
  settings or the limits catalogue, and if it does not go there now, say why.
- **A second source of truth.** A constant beside a setting that names the same thing is the defect
  this project has shipped most often, and it is invisible: the panel shows a value, the code reads
  the constant, and nothing errors. **Six of the last seven incidents were this one defect**
  (ROADMAP 6ah): a profile-ready flag, a daily cap defined four times, a health count at a threshold
  nobody delivered at, an applicant ceiling, two page limits, and a retrain gate.
  - `services` may not import `services`, so the tempting fix is a constant on each side plus a test
    asserting they are equal. **That test passes while both are wrong** — equality is not agreement.
    Two identical `= 5`s once asked different questions: one an absolute count, one a delta.
  - **A value needed on both sides of a service boundary goes in `libs`.** One definition, imported
    by both, with no copy to drift. That is what `libs.limits`, `libs.settings` and
    `libs.scoring.thresholds` are for.
- **A value with no consumer** — a setting, timer, column or list nothing reads.
- **A fallback that swallows a real answer.** `resolve(...) or DEFAULT` cannot express "unlimited",
  and an account set to Unlimited silently ran at the default for as long as that line existed.
- **Anything load-bearing that only works by luck**: an order dependency, a shared mutable, a
  hardcoded id, a path that assumes one tenant.

Report them as a short list with a recommendation each — expose it, delete it, or leave it and why.
The owner decides; the job is to make sure the decision is available rather than discovered later in
production.

## 6. Prefer fewer failure points to more tests

**Counted on 2026-08-05: sixteen defects in one week, and only two were wrong code.** The rest were
relationships — one value defined twice, a column nothing wrote, a step allowed to outlive somebody
else's clock. The suite could not catch any of them, because every component was individually
correct and the fault was *between* components.

So when a change adds a moving part, the question is not "can I test it" but **"can I not add it"**:

- **A second clock is a second outage.** Anything with an interval, a timeout or a budget derives it
  from the one already there. There were thirteen independent time bounds across systemd units and
  Python, and `linkedin.service` was allowed to run for thirty minutes on a fifteen-minute timer —
  which is not a delay but a cancelled tick.
- **A value needed in two places goes in `libs`.** Never two constants and a test asserting they are
  equal: that test passes while both are wrong (§5).
- **A column, setting or row is a promise.** If nothing writes it, the feature that reads it is
  silently absent — no error, no query failure, just NULL forever.
  `tests/test_columns_have_producers.py` fails the build on a new one.
- **Every hop is a place to stop.** Harvest → dedup → store → describe → embed → score → deliver are
  seven real state changes and worth keeping; five timers, seven lanes and fifteen services around
  them are not all load-bearing. Removing a hop is worth more than covering it.

The tests that have paid for themselves here are the ones asserting a *relationship*
(`test_schedules.py`, `test_limits_are_wired.py`, `test_columns_have_producers.py`), not the ones
asserting a function returns what it returns.

## 7. Green before it is pushed — locally, because that is the only gate now

**GitHub Actions is unavailable to this project.** As of 2026-08-06 every job on every run fails at
setup with `Failed to resolve action download info: Service Unavailable` — including jobs that do
nothing but check import boundaries — and runs sit queued for quarter-hours before doing it. That is
not a signal about the code and it must not be read as one.

So the local gates are no longer a *prediction* of the check. **They are the check.** Nothing goes to
the VPS that has not passed all six here first, and there is no backstop behind them.

Before every push, locally, in this order:

```
lint-imports                       # services may not import each other; libs may not import services
ruff check .
alembic upgrade head               # from empty, on a throwaway Postgres
pytest tests/ -q                   # with DATABASE_URL set — read the skip count, not just the colour
python web/scripts/dump_openapi.py && npx openapi-typescript openapi.json -o src/api-types.ts
git diff --exit-code web/openapi.json web/src/api-types.ts   # the contract must already be current
npm --prefix web run build         # the site has to compile
```

The last three used to be CI's alone, and that is exactly why they are written out here: **a gate
nobody runs is a gate that does not exist.** The contract check caught a real break the day it was
added, and the two before it are what would have caught it earlier.

- **A skipped test is not a passing test.** Roughly 290 of them skip silently without
  `DATABASE_URL`, and the suite still prints green. Check the skip count, not just the colour.
- **Say which gate you ran and what it said, with the numbers.** "Gates green" is a claim; "1,340
  passed, 13 skipped, contracts 2 kept" is a report. Once CI stopped answering, the only thing
  standing between a broken push and production is whether that report is honest.
- **A gate whose result depends on which machine ran it is not a gate.** Every tool that decides pass
  or fail is capped in `requirements-dev.txt`, for the reason ruff's rules are pinned in
  `pyproject.toml`. Two instances of this defect, both of which reported green locally while the
  build was broken: `alembic>=1.13` let CI take 1.19 while a laptop held 1.18.5, and 1.19
  double-applies the check-constraint naming convention, so the drift gate failed **every run for
  four days** (ROADMAP 6al); and `dump_openapi.py` let Python choose the line ending, so a dump run
  on Windows committed 4,642 CRLF lines that CI regenerated as LF — a whole-file diff in which not
  one line had changed. Raise a cap deliberately, after running the gate against the new version.
- If a gate cannot be run, **say which one and why in the response** — do not push and hope.
- **New behaviour nothing runs is not covered.** A test needing a live stack belongs behind an
  explicit skip *and* a note; a test needing nothing must not sit in a file that skips itself
  wholesale, or it silently never runs. That has already happened here.
- **Batch commits and push once.** The reason used to be that Actions minutes are the owner's money —
  90% of a month's allowance went by the 5th. It still holds for the VPS deploy, and a docs-only
  change should ride with the next code change rather than travel alone.

**If Actions is restored**, this section goes back to reading "the local gates predict CI, read the
CI result, do not assume it" — and the six commands above stay, because they were always the ones
that mattered and only two of them were ever run by habit.

## 8. Logs are the only view into a running system

There is no debugger on the VPS. Whatever was not logged did not happen, and this project has
already lost two digests and six hours of ingestion to failures that raised nothing anybody saw.

**Log every exception at the boundary that swallows it.** A `try` that continues, a fallback, an
`or` default, a bare `except` — each is a decision to carry on with less, and each needs a line at
`WARNING` or `ERROR` naming what failed and what was done instead. An exception caught silently is a
bug that will be found by a user.

Levels, so that severity means something:

- `ERROR` — work was lost or a user is affected. Somebody should look today. Always with the
  exception, always with the identifier (user, source, job id) needed to find it again.
- `WARNING` — recovered, but not normal: a retry, a fallback, a source returning nothing, a limit
  reached. This is the level that finds the intermittent faults.
- `INFO` — one line per unit of work, with its outcome and a count. "notified X with 3 item(s)",
  "harvest: 412 rows from 18 sources". Enough to reconstruct what the platform did.
- `DEBUG` — off in production.

**Do not flood `INFO`.** A log nobody can read is the same as no log, and the failure it hides is
whichever one happened during the noise. Specifically:

- No per-item lines at `INFO` in a loop — log the tally after it, not the iteration inside it.
- Third-party loggers get their level raised, not their output tolerated. One `httpx` line per
  outbound request drowns the platform's own lines at exactly the moment they matter.
- A message that is always the same is not information. If it cannot be false, drop it.
- Counts and reasons over volume: `dropped {'too_old': 11, 'workplace': 1}` is one line and answers
  the question. Eleven lines saying "dropped" do not.

Never log a secret, a token, or a full proxy URL. `libs.secrets.redacting` exists for the case where
a traceback would otherwise carry one.

## Practical

- Full suite against a real Postgres before pushing — see rule 6 for the gates and the skip trap.
- `.venv/Scripts/python.exe` on the Windows box; bare `python` has no dependencies.
- `docs/saas/ROADMAP.md` is the plan and `CHANGELOG.md` is the history. A non-trivial change gets an
  entry in the second; a plan changes in the first.
