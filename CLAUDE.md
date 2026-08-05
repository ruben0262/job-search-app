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

## 6. Green before it is pushed

**Nothing goes to the VPS that CI would have rejected.** Pushing first and reading the result later
means broken code is live for however long the run takes, and on this project "live" means an
account stops receiving jobs.

Before every push, locally, in this order — the same gates CI runs, so a pass here is a prediction
of a pass there:

```
lint-imports            # services may not import each other; libs may not import services
ruff check .
alembic upgrade head    # from empty, on a throwaway Postgres
pytest tests/ -v        # with DATABASE_URL set
```

- **A skipped test is not a passing test.** Roughly 290 of them skip silently without
  `DATABASE_URL`, and the suite still prints green. Check the skip count, not just the colour.
- If a gate cannot be run locally, say which one and why in the response — do not push and hope.
- CI is the backstop, not the check. It runs the same four gates plus the embedder tier and the
  web/OpenAPI contract, and a red run after a push is a fix under time pressure that could have
  been a fix at leisure.
- **New behaviour that CI cannot see is not covered.** A test needing a live stack belongs behind an
  explicit skip *and* a note; a test needing nothing must not sit in a file that skips itself
  wholesale, or it silently never runs. That has already happened here.

## 7. Logs are the only view into a running system

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
