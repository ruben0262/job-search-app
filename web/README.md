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
| `src/api.ts` | every request, and the single-flight refresh |
| `src/App.tsx` | routing, and where a signed-in user belongs |
| `src/pages/Onboarding.tsx` | the wizard shell — the server says which page |
| `src/pages/onboarding/` | the seven pages |
| `src/pages/{Dashboard,Feed,Process,Settings,Privacy,Verify}Page.tsx` | after signup |
| `src/ui.tsx` | the ten components a library would have given us |

## Look

**Black, white, grey.** Two colours exist in the whole palette — a red for errors and a green for a
score — and both mean exactly one thing, which is why they read instantly. Everything structural is
a step on one grey ramp. Banners are told apart by a left rule and their words rather than by a
fill, because three coloured blocks on one page is a traffic light nobody reads.

**Light and dark, with a toggle on every page** including the ones you reach signed out. Three
states, not two: `light`, `dark`, and *unset* — unset is the default, so somebody whose system is
dark gets dark without being asked. Pressing the toggle leaves unset permanently; there is no way
back to "follow the system", because a three-way control is a puzzle in a corner nobody wanted.

**The attribute is set before first paint** by a snippet in `index.html`, not by a React effect. An
effect runs after the first frame, so a dark-mode viewer would see one white flash on every load.

Transitions are colour-only, 140 ms, one duration for everything — different speeds on one page
read as jitter — and `prefers-reduced-motion` turns them off entirely.

**No component library and no router.** This is a wizard plus four screens: `react-router` would be
more configuration than routing, and a component library would be a build-time dependency and a lot
of generated markup for markup that is already written. Two runtime dependencies, `react` and
`react-dom`, and a 69 kB gzipped bundle.

**A hash router, not history.** Cloudflare Pages then serves `index.html` for every URL with no
rewrite rule for anybody to forget.

## Two rules worth keeping

**Nothing here re-derives a server rule.** Which onboarding page you are on comes from
`GET /onboarding/state`; what your account needs next comes from `GET /dashboard`. Those rules exist
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

**All of the above shipped as decided.** `openapi.json` and `src/api-types.ts` are committed, and CI
regenerates and diffs them — an unexpected diff fails the build with the command to fix it. One
caveat worth writing down: FastAPI types most handlers as a bare `dict`, so the generated types
describe paths and request bodies but not response *contents*. The response shapes the components
read are narrow hand-written views at the bottom of `src/api.ts`. Annotating every handler with a
response model would close that gap and is the right fix if the drift ever bites; it has not yet,
and doing it pre-emptively would put presentation concerns into the API.

Restart blast radius: none (static).
