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
| Built, **not deployed** | `docgen` — the image has never been built on the box |
| Timers | `harvester` (15 min) and `backup` (03:20) enabled; **`health.timer` deliberately disabled** |
| Tests | 730 passing against a throwaway Postgres, CI 10/10 jobs green |

**Phases 0–4 complete.** Phase 5a (feedback loop) complete. Phase 5b (docgen) built and waiting on a deploy.

Both accounts are fully configured: passwords set, Telegram connected with per-category channel
routing, documents imported (Ruben 9 / 98,059 chars, Andras 11 / 243,819), models servable, feeds
scored.

### Why `health.timer` is off

Its silent-feed alert is a **true positive** while `notifier` is not running: both accounts have
matches waiting and no delivery. Enable it in the same change that starts delivery, or it emails
every 15 minutes about a condition we created deliberately.

---

## What is left, in the order it has to happen

**All three of the items below were built on 2026-07-28. What remains is not code:**

1. **Build and start `docgen` on the box**, then watch one real tap through (§1–2).
2. **Look at the redesigned overview in a browser** against a real account (§3).
3. **Decide when delivery starts.** `notifier` is built and stopped, and `health.timer` stays off
   until it runs — both accounts have matches waiting, so the silent-feed alert is a true positive
   until somebody turns delivery on.
4. **Cover letters** are the first thing with no design yet: `tailor_cover.md` is generalised and
   ready, and nothing says *when* a posting wants one. The reference instance read a
   `cover_letter_required` flag the platform's corpus does not have.

### 1–2. ~~The CV render and the docgen worker~~ — **done 2026-07-28, not deployed**

`libs/templates/cv.py` fills the template from `ProfileSchema`; `services/docgen` turns a tap into a
PDF. Both prompts are generalised — the SACRED change was approved on the day and is in the
changelog with its reasoning. Cover letters are deliberately not wired: `tailor_cover.md` is ready
and nothing yet says *when* a posting wants one.

**What is left is deployment**, and it is the only thing between a tap and a real PDF:

```bash
cd ~/job-search-app && git pull
docker compose -f deploy/compose.yml --profile full up -d --build docgen
docker compose -f deploy/compose.yml logs -f docgen
```

Then tap Build CV on a real match and watch one through. Things worth knowing before that first tap:

- **The image build fails if `fonts-lato` is missing** — deliberately. Chrome falls back to Arial in
  silence otherwise, which is a correct document in the wrong typeface.
- **~90–130 s and two Chrome compiles per build** on the user's own Claude credential, capped at 10
  builds a day by the free plan's `cv_builds_per_day`. The quota is refused at the tap, with a popup
  naming the limit.
- **A failed compile retries; everything else dead-letters.** Chrome running out of memory on a busy
  box is transient, and `mem_limit: 1500m` is what keeps a bad build from taking the API with it.
- **Nothing is delivered if the output contains hidden content.** Fail closed, on purpose.
- The real-Chrome test is skipped unless a browser is present: `CHROME_BIN=… pytest tests/test_docgen.py`
  runs it, and it is the only check that says the layout survives a real render.

### 3. ~~Dashboard redesign~~ — **done 2026-07-28**

The backend was already state-driven; the UI was three bordered panels of equal weight, so the one
action worth taking looked exactly like the delivery settings. Now: state as the headline, the
server's explanation under it, one control, and the numbers as figures underneath
(`web/src/pages/DashboardPage.tsx`, `.overview` / `.stats` in `styles.css`).

**Not looked at in a browser** — `npm run build` typechecks and builds, and nobody has seen it
rendered against a real account. That is worth five minutes before it counts as finished.

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
