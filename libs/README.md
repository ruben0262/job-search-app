# libs — shared code

The only packages a service is allowed to import from.

```
services/*  ──imports──▶  libs/*
     │                        │
     └── never ───────────────┘   libs never imports services
     └── never imports another service
```

Services reach each other over HTTP or the queue, never by import. Enforced by `import-linter` in CI
([`pyproject.toml`](../pyproject.toml)), not by discipline — without it, eight independently
deployable containers become one program in eight folders.

Code here stays free of service-specific assumptions: no reading a service's env vars, no reaching
for a service's session, no knowing which container it runs in.

## Current state

| Package | State | What it is |
|---|---|---|
| [`auth/`](auth/) | ✅ **built** | argon2id, hashed email tokens, rotating refresh sessions (`AUTH_REUSE.md` design port) |
| [`mail/`](mail/) | ✅ **built** | Gmail SMTP sender + plain-text templates; prints instead of sending when unconfigured |
| [`db/`](db/) | ✅ **built** | SQLAlchemy models + Alembic migrations. 11 tables, 3 revisions, applied to the live database |
| [`corpus/`](corpus/) | ✅ **built** | Dedup keys (T2-pinned), corpus repository, ATS token/ETag state |
| [`tenant/`](tenant/) | ✅ **built** | Per-user repository: job state, applied log, labels, feedback. **T1 green** |
| [`contracts/`](contracts/) | ✅ **built** | `UserConfigSchema` (server validation + JSON Schema for the form) and the embedder client contract. **T9 green** |
| [`secrets/`](secrets/) | ✅ **built** | Envelope encryption (KEK/DEK), credential store, log redaction. **T4 green** |
| [`scoring/`](scoring/) | ✅ **built** | Rubric render + `profile_version`, and the per-user feature extractor |
| [`queue/`](queue/) | ✅ **built** | Four-lane claim protocol + the worker loop. **T5/T6/T7 green** |
| [`prompts/`](prompts/) | partial | `label_template.md` (SACRED) + the loader. Generation prompts land with docgen |

Empty packages are not placeholders for their own sake — each `__init__.py` records why the package
exists and which constraints it carries, so the tree is navigable before it is populated, and so the
boundary contract covers a package the moment it has code in it.

## The two that carry special rules

**`scoring/features.py` must never be duplicated.** `services/trainer` and `services/scorer` are
separate containers that must agree byte-for-byte on feature construction. Two copies that drift do
not crash — they silently score against different semantics, which is worse than an outage because
nothing reports it. See `docs/saas/CODE_STRUCTURE.md` §4.

**`prompts/` is SACRED.** No edit without explicit approval, A/B measured, changelogged. That now
also covers `label_template.md`, because editing it re-versions *every* user at once.

**`secrets/` never gains a method that returns a plaintext to a response.** Reads return `Secret`,
which does not stringify; the API layer's only read path is
`TenantRepository.secret_metadata`, which selects `last_4`. Adding a "just for debugging" accessor
is how write-only storage stops being write-only. `docs/saas/TENANT_SAFETY.md` §2.

## Running the tests

```bash
pip install -r ../requirements-dev.txt
pytest ../tests/                 # schema tests skip without DATABASE_URL
lint-imports                     # boundary contracts
```
