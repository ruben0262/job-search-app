# web — React + TypeScript SPA

**Built 2026-07-28** (Phase 3 step 12), after the API stopped moving. Building it first would have
meant building the product twice, and every form here is a form against a settled endpoint.

```bash
npm install
npm run dev      # proxies /auth, /onboarding, ... to a local API on :8000
npm run types    # regenerate openapi.json + src/api-types.ts after an API change
npm run build    # tsc --noEmit, then vite build
```

## Shape

| File | What it owns |
|---|---|
| `src/api.ts` | every request, the single-flight refresh, and `Schema<"Name">` for the generated types |
| `src/App.tsx` | routing, the five places (a tab bar on a phone), and where a signed-in user belongs |
| `src/pages/Onboarding.tsx` | the three screens: your CV, what we found, done |
| `src/pages/{Jobs,Words,Documents,Settings}Page.tsx` | the places after signup; Help is the support chat's |
| `src/documents.ts` | a CV or cover letter for a job: start it, show it being made, hand over the PDF |
| `src/alerts.tsx`, `src/push.ts` | Telegram and this browser's notifications, one tap each |
| `src/ui.tsx` | the pieces every page needs: word chips, the upload button, errors |

## Look

**Black, white, grey.** Two colours exist in the whole palette — a red for errors and a green for a
score — and both mean exactly one thing, which is why they read instantly. Everything structural is
a step on one grey ramp. Banners are told apart by a left rule and their words rather than by a
fill, because three coloured blocks on one page is a traffic light nobody reads.

**No containers on the two pages that ask something of you.** The onboarding wizard and the
overview are title, one line of context, the one thing to do, and then the quiet detail — a box
drawn around a question competes with the question. The overview was three bordered panels of equal
weight until 2026-07-28, which made "process your documents" look exactly like the delivery
settings; the panels remain where a page genuinely holds several unrelated things (the feed, the
privacy disclosure, the settings forms).

**Dark by default, light by choice** (changed 2026-07-30). The landing page at
mahoraga.rubenlekli.com is dark-only, and the app opening in white read as a different product — so
the app carries the landing's exact tokens (`#060606`, hairline borders, mono uppercase kickers,
pill buttons, the faint dot-grid backdrop) and dark is the default regardless of the system
preference. The toggle offers light, an equivalent inverse, and the choice sticks.

**The attribute is set before first paint** by a snippet in `index.html`, not by a React effect. An
effect runs after the first frame, so a dark-mode viewer would see one white flash on every load.

Transitions are 0.25 s, one duration for everything — different speeds on one page read as
jitter — plus a single fade-up when a route changes, and `prefers-reduced-motion` turns all of it
off entirely.

**No component library and no router.** This is a wizard plus four screens: `react-router` would be
more configuration than routing, and a component library would be a build-time dependency and a lot
of generated markup for markup that is already written. Two runtime dependencies, `react` and
`react-dom`, and a ~74 kB gzipped bundle.

**A hash router, not history.** Cloudflare Pages then serves `index.html` for every URL with no
rewrite rule for anybody to forget.

## Two rules worth keeping

**Nothing here re-derives a server rule.** Which onboarding screen you are on, what your account
needs next and whether an AI can write for you all come from `GET /dashboard`. Those rules exist
once, in `libs/onboarding` and `services/api`, and a second copy here would be the one that goes
stale — the first symptom being a user stuck on a page they have already finished.

**One refresh at a time.** A dashboard load fires several requests; if each refreshed on its own
401, the second would replay a token the first had rotated, and the server treats a replayed
refresh token as theft and kills the whole session family. `src/api.ts` shares one promise.

## What is already decided

- **React + TypeScript** (decided 2026-07-26). The backend stays Python — the CPU-bound part is
  already offloaded to the Rust TEI embedder, so the "switch languages for speed" argument does
  not apply (`SAAS_PLATFORM_PLAN.md` §3).
- **Cloudflare Pages, on a subdomain of `rubenlekli.com`** (decision D7). The API is reached
  through a Cloudflare Tunnel, so the VPS needs **no open inbound ports**. Free tier covers this
  scale and it reuses existing Cloudflare experience.
- **The API client is generated from the OpenAPI spec FastAPI already produces — never
  hand-written** (`CODE_STRUCTURE.md` §8). The point is that frontend and backend *cannot* drift:
  a changed response shape becomes a TypeScript compile error rather than a runtime `undefined`
  in production. The same generator run types the config form, so `libs/contracts/config.py`
  stays the single definition behind both server validation and form fields.
  Commit the generated client so CI can diff it — an unexpected diff means someone changed a
  contract without meaning to.
- Wire the generator at **Phase 3**, when the API surface first stabilises. Generating against a
  churning API means regenerating constantly for no benefit.

**All of the above shipped as decided.** `openapi.json` and `src/api-types.ts` are committed, and the
contract gate regenerates and diffs them. **Every response the site reads is generated, too**
(2026-10-29). A handler returning a bare `dict` is an untyped object to the generator, and that is
how the place pickers rendered empty for two months: a hand-written view of a field the server had
stopped sending. So every endpoint a page reads declares a `response_model`, and the page reads
`Schema<"Name">`: a field the server drops fails `tsc`, not the page.

Restart blast radius: none (static).
