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
  the constant, and nothing errors.
- **A value with no consumer** — a setting, timer, column or list nothing reads.
- **A fallback that swallows a real answer.** `resolve(...) or DEFAULT` cannot express "unlimited",
  and an account set to Unlimited silently ran at the default for as long as that line existed.
- **Anything load-bearing that only works by luck**: an order dependency, a shared mutable, a
  hardcoded id, a path that assumes one tenant.

Report them as a short list with a recommendation each — expose it, delete it, or leave it and why.
The owner decides; the job is to make sure the decision is available rather than discovered later in
production.

## Practical

- Full suite against a real Postgres before pushing — ~290 tests skip silently without
  `DATABASE_URL`, so a green run without it means very little.
- `.venv/Scripts/python.exe` on the Windows box; bare `python` has no dependencies.
- `docs/saas/ROADMAP.md` is the plan and `CHANGELOG.md` is the history. A non-trivial change gets an
  entry in the second; a plan changes in the first.
