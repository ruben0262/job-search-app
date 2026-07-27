# web — React + TypeScript SPA

Placeholder. Built in **Phase 3** (self-service onboarding); nothing user-facing ships before the
shared core exists, and building the UI first means building the product twice.

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

Restart blast radius: none (static).
