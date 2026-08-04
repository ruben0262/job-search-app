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

## Practical

- Full suite against a real Postgres before pushing — ~290 tests skip silently without
  `DATABASE_URL`, so a green run without it means very little.
- `.venv/Scripts/python.exe` on the Windows box; bare `python` has no dependencies.
- `docs/saas/ROADMAP.md` is the plan and `CHANGELOG.md` is the history. A non-trivial change gets an
  entry in the second; a plan changes in the first.
