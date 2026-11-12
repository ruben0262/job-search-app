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

What is here is what runs: what the platform no longer uses is in [`retired/`](../retired/) (2026-11-03).

| Package | What it is |
|---|---|
| [`assistant/`](assistant/) | The help chat: a reply written by an AI on the key that pays, streamed into its row |
| [`auth/`](auth/) | Password hashing, verification tokens and refresh sessions |
| [`contracts/`](contracts/) | Pydantic models shared between services: the search config and onboarding |
| [`corpus/`](corpus/) | The shared job corpus: dedup keys, storage, places, salaries, reading a job's page |
| [`credentials/`](credentials/) | Checking an API key with its provider the moment it is linked |
| [`db/`](db/) | MongoDB: one client per process, and every collection's fields and indexes |
| [`documents/`](documents/) | Uploaded documents as text, and the CVs and cover letters kept |
| [`harvest/`](harvest/) | Vocabulary the harvest and the admin panel both need |
| [`legal/`](legal/) | The disclosures, and whether a person agreed to them |
| [`limits/`](limits/) | Every ceiling on one account, and who may change it |
| [`llm/`](llm/) | The one seam to the AI providers: Anthropic, Google and OpenAI, on an API key or a signed-in account's own tool; each person's AI accounts and the one in use |
| [`mail/`](mail/) | Transactional email |
| `membership.py` | Who has a membership: paid or in its trial (Stripe), free access from an admin, or an admin. One filter, for what members get: every job at once (`delay_hours`), documents, alerts |
| [`notify/`](notify/) | In-app notifications, and the new matches owed to a person off the site |
| [`obs/`](obs/) | Logging for every process |
| [`onboarding/`](onboarding/) | What a new account must supply before it has jobs |
| [`ops/`](ops/) | Seeing the platform, and being told when it needs a person |
| [`profiles/`](profiles/) | A person's profile as files, and what a document is grounded on |
| [`prompts/`](prompts/), [`scaffolds/`](scaffolds/) | The generation prompts, and the files a new account starts from (SACRED, below) |
| [`queue/`](queue/) | The job queue: claim, retry, dead-letter |
| [`secrets/`](secrets/) | Envelope encryption for each person's credentials (KEK/DEK), and log redaction |
| [`signin/`](signin/) | Signing a person in to their own Claude, ChatGPT or Gemini account through that provider's own tool (the `oauthbroker` service) |
| [`settings/`](settings/) | The operator's settings and their defaults |
| [`templates/`](templates/) | The CV layout, and its loader |
| [`tenant/`](tenant/) | Per-user data: job marks, applications, config |
| `matching.py` | Which jobs a person sees: their green words pick them, their red words veto them |
| `proxy.py` | The site's one proxy |
| `suggestions.py` | The green and red words a person might want, with the reason. No AI |
| `telegram.py` | Telegram's Bot API, for a person's own bot |

## The rule that carries special weight

**`prompts/` is SACRED**, and so is `scaffolds/` (CLAUDE.md §3a). No edit without explicit approval,
A/B measured, changelogged.

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
