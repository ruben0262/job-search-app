# Handover — 2026-07-28

Where the platform actually is, what is left, and the decisions a new session would otherwise have
to re-derive. `ROADMAP.md` is the plan; this is the position.

---

## Live right now

| | |
|---|---|
| Site + API | `https://mahoraga-app.rubenlekli.com` (Cloudflare Tunnel; port 22 is the only public listener) |
| Box | `ruben@169.58.16.241`, checkout at `~/job-search-app` |
| Running | `postgres`, `capture`, `labeller`, `scorer`, `api`, `cloudflared`, embedder (own project) |
| Built, **not started** | `notifier`, `trainer` — starting the notifier messages real people |
| Timers | `harvester` (15 min) and `backup` (03:20) enabled; **`health.timer` deliberately disabled** |
| Tests | 672 passing, CI 10/10 jobs green |

**Phases 0–4 complete.** Phase 5a (feedback loop) complete. Phase 5b (docgen) partially started.

Both accounts are fully configured: passwords set, Telegram connected with per-category channel
routing, documents imported (Ruben 9 / 98,059 chars, Andras 11 / 243,819), models servable, feeds
scored.

### Why `health.timer` is off

Its silent-feed alert is a **true positive** while `notifier` is not running: both accounts have
matches waiting and no delivery. Enable it in the same change that starts delivery, or it emails
every 15 minutes about a condition we created deliberately.

---

## What is left, in the order it has to happen

### 1. Render the CV body from the profile — **blocks everything else in docgen**

**Decided 2026-07-28: one template, `dev`, adapted per job.** `libs/templates/base_cv.html` is in
the repo with its **identity already removed** — header is `<!--NAME-->` / `<!--CONTACT-->`, and a
test asserts no name, address, phone, email or link survives.

**What is left is the body.** It still carries the previous owner's profile paragraph, experience
and education. Those become markers filled from `ProfileSchema`, the same deterministic-render job
as `libs/scoring/rubric.py` — see `libs/templates/README.md` for the marker-to-field table.

The prompts were generalised on 2026-07-28 (`<<NAME>>`, `<<LANGUAGES>>`, `<<WORK_ELIGIBILITY>>`,
`<<HOME_LOCATION>>`, `<<CONTACT>>`, filled by `libs/scoring/docprompt.py`). **The templates were
not.** So a docgen worker built today would produce a correct-looking CV with the wrong person's
details on it.

Do the same thing that was done to the prompts: replace the subject, change nothing else. The layout
is load-bearing — A4 page rules, font paths, the `@page` margin rule that stops Chrome drawing a
header over the content (`PROFILE_CAPTURE_PLAN.md` §6.5: *buy, do not generate*).

`tests/test_prompt_templates.py` already covers the template's identity and the layout rules that
are load-bearing (`@page`, the header classes) — losing those during a generalisation pass is the
silent kind of damage.

### 2. The docgen worker

The `docgen` queue lane has existed since Phase 1 with **no worker** — the same shape of gap the
`score` lane turned out to be. "Build CV" taps from Telegram already enqueue correctly and wait.

Needs: Chrome in its own image (`CODE_STRUCTURE.md` §5 — the only image with Chrome), ~90–130 s per
build, PDF delivered to the user's personal chat and never the feed channel, documents **ephemeral**
(D9: built, delivered, discarded; the applied-log is the durable record).

Port from `d:/Projects/job-search/pipeline/cv_builder.py` (~993 lines).

### 3. Dashboard redesign

Untouched. The backend (`GET /dashboard`) is state-driven and complete; the UI is functional, not
designed. Same direction as the onboarding pass: black/white/grey, no containers, one idea per
screen.

---

---

## Decisions a new session should not re-litigate

- **The landing page stays in `mahoraga-jobs-web`.** It was copied in on 2026-07-28 and removed the
  same day: copying it meant rewriting its copy to suit a hosted product, which is a change to that
  page made in the wrong repository. The app's sign-in is the entry point and the landing page links
  to it.
- **The API serves the SPA**, rather than Cloudflare Pages as D7 planned. D7's requirement was no
  open inbound port and the tunnel already provides that; a second origin adds a CORS policy that
  stays untested until the day it is wrong.
- **`notifier` is behind the `full` compose profile** so `up -d` never starts delivery by accident.
- **Backups stay on the box** (accepted risk, 2026-07-28) — covers a bad migration or a dropped
  table, not disk loss. There is nowhere off-box to put them yet.
- **Zoho EU, not global.** `smtp.zoho.com` answers `535 Authentication Failed` for
  `no-reply@flow-solutions.xyz`; `smtp.zoho.eu` authenticates. The error is indistinguishable from a
  wrong password.
- **`max_notifications_per_day` is out of the onboarding form**, kept in the schema with its
  default. It is a plan limit, not a search preference, and becomes a tier setting when Stripe
  arrives (the seam is the `plans` table — adding a tier is an INSERT).

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

Every failure that reached CI in this build was catchable by that loop or by
`alembic upgrade <rev>:head --sql`, which renders a migration offline.
