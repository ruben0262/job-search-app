# tests

Behaviour tests for the whole platform (CLAUDE.md §2). Most need a MongoDB to run against; without
one they skip, and the suite still prints green — **read the skip count, not the colour** (§7).

## Running them

One command runs every gate of CLAUDE.md §7, the tests last:

```bash
scripts/gates.sh            # before a commit: every gate, and only the tests the change can reach
scripts/gates.sh --all      # every gate and every test
```

Or the tests alone, against a local `mongod`, **never Atlas**: the tests empty every collection they touch,
and `conftest.py` refuses a `MONGODB_URI` that names an Atlas cluster. Each run works in its own `test_<pid>`
database and drops it at the end.

```bash
export MONGODB_URI="mongodb://127.0.0.1:27017"
export CHROME_BIN=...                                   # a Chromium: without it every browser test skips
pytest tests/ -q -rs -n 4 --dist worksteal              # all of them
pytest tests/ -q -rs -n 4 --dist worksteal --affected   # only what the uncommitted work can reach
```

Set those two variables and nothing else from `certificates/dev.env`: the rest of that file switches LinkedIn
off, and two dozen tests fail for it. A standalone `mongod` is enough: nothing uses a multi-document
transaction. The one the README's "Run it on your computer" starts will do.

`-n 4 --dist worksteal` runs it on four workers that take work from each other (`pytest-xdist`). The
`worksteal` matters: the browser tests are most of the time and sit together in one file, and dealt out in
fixed shares they queued behind one worker while three sat idle. Each worker is a process of its own, so it
has a `test_<pid>` database and ports of its own. Four, not one a core: every browser test runs a Chromium and
a server, and memory runs out before the cores do. Leave `-n 4` off to run them one at a time, which a test
that fails only in company is worth doing.

All of it took 10 min 55 s on 2026-11-26 (2,114 tests, on mains) and 4 min 50 s on 2026-11-27 (1,992, on
battery with the processor held at a quarter of its speed, so a floor): CHANGELOG 2026-11-27 has what changed.
Six workers were no quicker than four.

The browser tests (the end-to-end walk from sign-up to a downloaded CV, and the PDF of every look) also need
a fresh `npm --prefix web run build`; the walk refuses a build older than its sources.

## Only what a change can reach: `--affected`

`pytest --affected` runs the test files the uncommitted work can reach, and `--affected=REV` those reached by
everything since a commit. `affected.py` picks them off the code as it stands, with nothing recorded to go
stale:

- a changed file reaches every test file that **imports it**, through any number of modules (and a file that
  is not Python reaches the tests of the modules beside it, which are what read it);
- a test that **names a top folder as a path** (`ROOT / "deploy"`, `"web"`) is reached by every change under
  it: that is how the tests that read the tree are picked;
- the fixtures, pytest's settings, the pinned versions, or a Python file deleted or moved, reach every test.

Beside its counts it says what it did (`affected: 14 of 92 test files`), and names any changed file no test
reaches (`no test reaches: ...`): that file was not tested. `test_affected.py` holds it.

## The browser walk runs with motion off

Every screen of the site slides in, and a browser driver waits for a button to stop moving before it presses
it: about a second a click, six seconds a test before it tested anything. So every context in
`test_e2e_site.py` is opened with `**STILL` (`prefers-reduced-motion`, under which the site's stylesheet
switches every animation off). A test about motion itself opens its context without it, and says why.

## What skips, and why

- `test_billing_stripe.py` — five tests against Stripe's own test mode, when `STRIPE_TEST_SECRET_KEY` and
  `STRIPE_PRICE_ID` are set. Nothing else runs them: run them when `stripe` is upgraded.
- Any test needing `MONGODB_URI` or `CHROME_BIN`, when they are not set.

## Fixtures

`conftest.py` gives `db` (the test database, emptied after each test, schema applied) and `user`
(one active account). Use them rather than opening a client: everything the platform does goes
through `libs.db.database()`, which reads the same `MONGODB_DB` the fixture sets.
