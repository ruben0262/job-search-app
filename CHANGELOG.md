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

## 2026-07-30

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
