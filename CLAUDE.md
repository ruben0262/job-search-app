# Working rules for this repository

Standing instructions, set by the owner. They apply to every change, not to a particular feature.

## 0. Anyone can use it (added 2026-10-28)

Built for someone who has never used a job tool, will not read instructions, and gives up at the
first confusing screen.

- **One screen, one job.** Every page has one main button and says what it does in words a child
  can read. A page that needs a how-to is wrong.
- **Rules before AI.** If code can decide it (filters, suggestions, ranking, reading a CV), code
  decides it. AI only writes documents and answers chat.
- **Fast or visible.** Every click answers within 1 s or shows progress. Jobs page < 300 ms; a CV
  streams while it writes and is done < 20 s.
- **Never leave the site to finish a step.** Telegram and notifications are optional, one tap.
- **Fewer lines wins.** When two versions pass the same tests, keep the shorter. Added lines must
  remove a moving part or fix a measured problem.

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
- **A test of wording or looks is not a test of the site** (owner, 2026-11-25: "we should be testing actual
  parts of the site not unimportant parts"). Nothing pins marketing copy, a title, a colour or a shape. Where
  a test only needs to know a page drew, it asks for the element, not its words.
- **No test waits a fixed moment** for something it does not control. It waits on the thing itself (an event,
  a row, the page saying so) or it is flaky by design, and a flaky test makes every run need a second one.
- **A slow test has to be crucial** (owner, 2026-11-26): a regression that cost something, or a flow nothing
  else walks. One that is neither goes; one that is gets made fast before it is kept slow. A grid of cases
  holds the cases that differ in kind, not every combination.

## 3. Delete the thing you replaced

- When a mechanism is reworked, the old one goes in the same commit. Dead code is not neutral: it
  gets read as current, imported by accident, and it hides which path is live.
- A setting, timer, column or list that nothing consumes is dead code with a friendly face. Find its
  consumer or remove it. Several have now been found by accident rather than by looking.
- Removing something is a decision that belongs in the commit message with its evidence, so it can
  be reversed by a person who was not there.

### The one exception: the switched-off model (owner's decisions, 2026-10-28 and 2026-10-30)

The learned scoring model (training, scoring, labelling, the AI profile capture, the setup agents
and the embedding step) was switched off on 2026-10-28. On 2026-10-30, when the platform moved
from Postgres to MongoDB, it was removed from the tree. It worked and may come back, so it is kept
in git rather than lost. **The tag `model-on-postgres` holds it**, with the Postgres schema it runs on.

- Restore it with `git checkout model-on-postgres -- <paths>`. Bringing it back means porting it to
  MongoDB, and it returns with its own tests.
- Do not rebuild it piecemeal from memory, and do not treat the tag as clutter to clean up.

Everything else still follows this section.

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

**A prompt is measured iteratively, on the model that will run it, against the wording it replaces.**
One generation is not a result. The output of a language model varies run to run, so a single sample
cannot tell a better prompt from a luckier one — and a prompt that reads better to a person is not
the same as a prompt that writes a better CV for a real applicant.

So before a wording change is adopted:

- **Run it several times over the same inputs**, not once. Enough samples that the spread is visible,
  and reported with the spread rather than as a single number.
- **On the model that will actually run it.** A wording tuned against a different model measures that
  model. Prompts here run on the model named in the calling module (`CV_MODEL`, `CAPTURE_MODEL`,
  `SETUP_MODEL`) — use it.
- **Against the previous wording, over the same postings**, scored the same way. The comparison is the
  evidence; an absolute score on its own is not.
- **Paired, and grouped where samples repeat.** Comparing two averages with their independent spreads
  hides any small consistent effect — that method nearly deleted a feature worth keeping (ROADMAP
  6bg). And where the same posting appears more than once, all its copies belong on the same side of
  a split, or the measurement scores the model on something it memorised: ~10% of this platform's
  labels are the same role listed in several cities, each with its own `content_key`.
- **Say what did not move.** A wording that improves one measure and worsens another has not been
  shown to be better, and reporting only the improvement is how a regression ships.

The number goes in `CHANGELOG.md` with the change, whichever way it came out. A measurement that
justifies *keeping* the existing wording is worth as much as one that justifies replacing it.

If a prompt looks like the cause of a defect, say so and propose the wording. Do not apply it.

## 3b. What a CV or cover letter change must pass (owner, 2026-11-19)

The owner: "save some of these filters that we use for cv and cover letter optimizations into claude.md ...
if we ever optimize we can always use it to make sure we have high quality, then it passes the anti AI
language check, ATS check, other model quality checks as well."

Anything that can change what a person's CV or letter says — a wording (§3a), a model, an effort, Codex's
lean mode, a check that sends a document back — is run on the bench (`scripts/cvlab/run.py`, whose
docstring says how; the people in `scripts/cvlab/panel/README.md`) against what runs now, and adopted only
when it holds on every gate below. A gate that was not run is reported as not run, never as passed.

1. **Fair people.** The panel's people in different trades, levels and document quality, each with an "About
   you" as the app keeps it and typed as people type; and the held-out people the change was never tuned
   on. Letters are measured in English for now (owner, 2026-11-19). For letters each also has a weak letter and
   About you of their own (`scripts/cvlab/panel/letters`, `--overlay`): the site writes no letter without one, and
   most people's are weak (owner, 2026-11-20).
2. **A reviewer on each platform.** Claude's (`--judge`: Sonnet 5.5 reading `eval_cover.md` or `eval_cv.md`)
   and GPT's (`--judge-provider codex --judge-model gpt-6.2-astra --judge-effort medium`: it reads as high
   does, within high's own spread, for less of the plan; CHANGELOG 2026-11-20). Each leans to its own family,
   so a change is better only where both say so. Read their written fixes, not only the marks.
3. **No AI language.** No hit from the AI-language list in `services/docgen/checks.py`, which production
   sends back too. Read the phrases one model writes for many people (`scripts/cvlab/formulas.py`): a
   formula a recruiter would learn goes on the list.
4. **The production checks**, as the docgen worker runs them (`checks.cv`, `checks.letter`, invented
   numbers). Count the first drafts they send back: each is a second call on the person's plan.
5. **ATS, in every look** (`--every-look`): every check pypdf and pdfminer make, in all six looks.
6. **Every model a person can write with**, as production runs it: Claude Haiku 5.5 at `build.EFFORT` and
   ChatGPT's Sol (lean for letters, `build.LEAN`); Gemini is left out for now (owner, 2026-11-21: "we only need
   gpt and claude"). A change measured on one model has not been measured on the other.
7. **Cost**: tokens in and out and seconds a document, by model. A change that costs more says what it
   bought.
8. **Language**, when letters in other languages are measured again: blind, coded reads by native-level
   readers, every letter of a posting to the same reader, each letter read twice.
9. **The hiring manager's read** (owner, 2026-11-21: "reverse engineering the cover letter as a hiring manager
   would want to see it"). `--reviewer hire` (`scripts/cvlab/eval_hire.md`), Claude's and GPT's as in gate 2: it
   works out from the title and the posting what the hire must show, then the best case this person's whole record
   can make, and marks how much of it the letter makes, its first lines, its proof, the doubts it settles, whether
   it stands out from the pile and its accuracy, with the chance of an interview. Selling a person is making their
   best true case, never a bigger one.
10. **Nothing bigger than their record** (owner, 2026-11-21: "make sure the strong points and facts about a user
   are indeed correct"). No rank or label the record does not give ("main languages" for two of the five they
   used), no part of a list as all of it, nothing the posting asks for claimed as theirs. Counted from both
   reviewers' lines not theirs (`not_theirs`), each read by a person, and on the trap: a developer with Python and
   no Go, for two postings that ask for Go (`backend-dev`). A change with more of them is worse, whatever else it
   raises. This is about facts. Why they want the job may be reasoned out (owner, 2026-11-27: "a logical and
   calculated inference", the reason "that sells the user best"): a reason, never a fact about their past.

Then §3a's rules: paired by person and posting, ± one standard error, what did not move, the numbers in
`CHANGELOG.md`; a wording not made live kept in `libs/scaffolds/history/` with its README row.

**A run that spends a person's plan is checked on its first document, not its last** (owner, 2026-11-20, after
72 ChatGPT letters were written plain but kept as lean: twice the tokens, a plan's five hours and a night lost).
The bench writes the first one alone and stops if it took far more than those kept for its model (`_FirstPaid`);
read that line and the first minutes of every stream before leaving a run. What a record or folder says about
how a document was made comes from the call that made it, never from a second rule beside it.

## 3c. How a prompt is worked on (owner, 2026-11-27)

The owner: "carry on the testing and developing on any prompt in the future the same way, where we perform
research on how everything should work, what models should be used for testing, and spot how we can get there
from where we are, then make sure evaluation is here rather than to make any manual testing facility, and make
sure that we run it through all the filters as well".

The method is the `prompt-lab` skill (`.claude/skills/prompt-lab/`), for the letter, the CV and any prompt
after them. Its order is the rule:

1. **What the document is for, first.** Researched, turned into targets and a list of mistakes that must not
   come back, and agreed with the owner before any wording. Six letter wordings were tuned, carefully, against
   the wrong idea of what a cover letter is for.
2. **The gap, and whose it is**: the shape the prompt asks for, an input it never gets, something code can do
   (§0), or a check that sends back what is now wanted. Every check and reviewer is read against the targets
   before anything is tested.
3. **Judged by the session reading it**: every document of a look (`scripts/cvlab/look.sh`, the sets in
   `scripts/cvlab/looks/`), a few people in different trades and the trap, written by both writers of gate 6 as
   production calls them, each fact traced to the person's record. The reviewers of gates 2 and 9 are run when
   the owner asks for the paired bench, their rubrics checked against the targets first; otherwise they are
   reported as not run.
4. **And beside the wording it would replace, by a reader who does not know which is which** (owner,
   2026-11-28, of v41, made live the night before on a read of its letters alone: "the v37 read much better").
   `scripts/cvlab/look.py blind` sets the two wordings' documents for the same person side by side, coded; one
   fresh agent ranks them (`references/blind-read.md` in the skill); `look.py unblind` says which won. A
   candidate is offered only where it is ranked at or above the live wording on both writers.
5. **Every filter before it is offered**: gates 3, 4, 5, 7 and 10, and the `/no-ai-language` checker where the
   machine has it.
6. **Read on people it was never changed for** before it goes live, and the report says whether it was.

§3a still decides what goes live: the owner's yes to a named version.

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

## 7. Green before it is committed, locally, because that is the only gate

**GitHub Actions is switched off (owner, 2026-11-26).** `.github/workflows/ci.yml` starts only when somebody
starts it by hand from the Actions tab, so it spends none of the account's minutes. It had failed at setup on
every run since 2026-08-06, and by the time it was looked at again it asked for a Postgres and five services
that had been gone for a month. It now runs the file the local gates are, so it has no list of its own to
drift; to switch it back on, give its `on:` a `push` again.

So the local gates are not a *prediction* of the check. **They are the check.** Nothing is committed that has
not passed them here, and there is no backstop behind them.

One command, which runs them in this order and stops at the first that fails:

```
scripts/gates.sh            # before a commit: every gate, and the tests the change can reach
scripts/gates.sh --all      # every gate and every test
```

What it runs:

```
lint-imports                       # services may not import each other; libs may not import services
ruff check .
python -m libs.db --prune          # the schema, on an empty database of a local mongod
web/scripts/dump_openapi.py, openapi-typescript    # written again, the API's contract must not change
npm --prefix web run build         # the site has to compile, before pytest, which walks the build
pytest tests/ -q -rs -n 4 --dist worksteal [--affected]   # a local mongod, never Atlas; read the skip count
```

**Before a commit, only the tests the change can reach (owner, 2026-11-26: "stop running the full suite all
the time ... only run parts that can be affected").** `pytest --affected` (`tests/affected.py`) picks them off
the code as it stands, with no recorded map to go stale: a changed file reaches every test file that imports
it, through any number of modules, and every test that reads its part of the tree (`"web"`, `"deploy"`: how the
relationship tests of §6 are picked). Checked against what each test file was seen to open while it ran:
1,909 reads by 91 test files, none missed (CHANGELOG 2026-11-27).

- **The line beside its counts is part of the report.** `affected: 14 of 92 test files` says how much ran, and
  `no test reaches: <file>` names a changed file nothing covers. That file was not tested, whatever colour the
  run is: say so, and say whether it needs a test.
- **All of it (`--all`) when the change is wide.** The pick says so by itself for what it can see: the
  fixtures, pytest's settings, the pinned versions, a Python file deleted or moved. It cannot see what is not
  in the tree: a package installed or upgraded here, another Chromium, another MongoDB. After one of those,
  all of it.
- **Most changes still reach the browser walk**, which is most of the suite's time: anything under `web/`, and
  anything the API imports: 131 of the 150 commits before this rule. That is the pick being right, not being
  slow. So the suite itself is kept quick (all of it in under five minutes on a laptop on battery, from
  10 min 55 s on mains; CHANGELOG 2026-11-27), and a test added to the walk has to earn its seconds (§2).
- **A skipped test is not a passing test.** Most of them skip silently without `MONGODB_URI`, and the suite
  still prints green. Check the skip count, not just the colour. The script refuses to start without a
  Chromium, for the same reason: every browser test would skip.
- **Say which gate you ran and what it said, with the numbers.** "Gates green" is a claim; "affected: 31 of
  92, 742 passed, 6 skipped, contracts 2 kept" is a report. With CI off, the only thing standing between a
  broken commit and production is whether that report is honest.
- **A gate whose result depends on which machine ran it is not a gate.** Every tool that decides pass
  or fail is capped in `requirements-dev.txt`, for the reason ruff's rules are pinned in
  `pyproject.toml`. Three instances of this defect, each of which reported green locally while a clean
  machine was broken: `alembic>=1.13` let CI take 1.19 while a laptop held 1.18.5, and 1.19
  double-applies the check-constraint naming convention, so the drift gate failed **every run for
  four days** (ROADMAP 6al); `dump_openapi.py` let Python choose the line ending, so a dump run
  on Windows committed 4,642 CRLF lines that CI regenerated as LF, a whole-file diff in which not
  one line had changed; and PyYAML, which three deploy tests import, was declared nowhere and present here
  only by accident, so a machine with just `requirements-dev.txt` failed them (2026-11-26). Raise a cap
  deliberately, after running the gate against the new version.
- If a gate cannot be run, **say which one and why in the response**. Do not commit and hope.
- **New behaviour nothing runs is not covered.** A test needing a live stack belongs behind an
  explicit skip *and* a note; a test needing nothing must not sit in a file that skips itself
  wholesale, or it silently never runs. That has already happened here.
- **Batch commits and push once.** A push is a deploy to the VPS, and a docs-only change should ride with the
  next code change and not travel alone.

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

- `scripts/gates.sh` before a commit, against a local MongoDB (`tests/README.md`): rule 7 has the gates, when to run all of them, and the skip trap.
- `.venv/Scripts/python.exe` on the Windows box; bare `python` has no dependencies.
- `docs/saas/ROADMAP.md` is the plan and `CHANGELOG.md` is the history. A non-trivial change gets an
  entry in the second; a plan changes in the first.
