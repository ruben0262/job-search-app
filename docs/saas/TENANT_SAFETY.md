# Tenant safety — isolation, secrets, quotas, config validation

**Status:** design, 2026-07-26. Everything here is a launch blocker. The shared-data architecture
(`PLATFORM_ARCHITECTURE.md` §2) trades container-level isolation for application-level isolation, and
this document is the other side of that trade.

---

## 1. Isolation model

Container isolation is gone by choice. What replaces it:

1. **Every per-user table carries `user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE`.**
   No exceptions, including join tables and queue rows.
2. **No user data in the shared corpus, ever.** The corpus holds public job postings only. This is
   what makes account deletion correct by construction (§6) and is worth defending in review.
3. **Repository layer takes `user_id` as a required argument** — not an optional filter applied by the
   caller. A query that forgets the tenant should fail to compile/typecheck, not return another
   user's rows. Concretely: no `get_scores()` that optionally filters; only `get_scores(user_id, ...)`.
4. **One release-blocking test** (`TEST_STRATEGY.md` T1) that seeds two users with overlapping jobs and
   asserts every read path returns only the caller's rows.

Consider Postgres row-level security as a second layer once the schema settles. Not v1 — it is a
belt-and-braces addition, and a wrong RLS policy is its own failure mode.

## 2. Secrets

Users hand over a Claude credential (API key and/or OAuth — decision D2), a Telegram bot token, and a
proxy credential (required for LinkedIn-sourced results — decision D3, `HARVESTER_PLAN.md` §5). These
are live credentials belonging to strangers.

Under the revised D3 the proxy credential is **used strictly for its owner's own fetches** — the
harvester's per-user LinkedIn loop decrypts it, uses it for that user's queries only, and discards it.
No credential is ever used on another user's behalf, which is what removes both the free-rider problem
and the provider-ToS exposure.

### Storage — envelope encryption

```
KEK  (master key)      — from environment / secret manager, NEVER in the database, NEVER in a backup
 └── DEK (per user)    — random, stored wrapped-by-KEK on the user row
      └── secrets      — each ciphertext stored with its own nonce
```

- Implementation: `cryptography` AES-GCM (or Fernet). Per-user DEK so a single compromised secret
  cannot decrypt the whole table, and so rotating one user is possible.
- **Design goal: a stolen database dump is useless.** That requires the KEK to live outside the
  database *and* outside its backups. Verify this explicitly — it is the single most common way this
  design is implemented and then quietly defeated by backing up the environment file alongside the DB.
- Decrypt only inside the worker that needs the credential, in memory, for the duration of the call.
  Never on the `api` service unless it is the one making the call.

### Handling rules

| Rule | Enforcement |
|---|---|
| Secrets are **write-only** through the API | Response schemas return `last_4` and `updated_at`, never the value. Tested (T4) |
| Never logged, never in error messages | Redaction filter on the logger; a test asserts a known secret cannot appear in captured logs |
| Validated at save time | Make a real minimal API call. A bad key must fail at the form, not silently at 3 a.m. |
| Rotatable | `PUT` replaces; old ciphertext deleted, not versioned |
| Destroyed on account deletion | Cascade + explicit overwrite |

### Telegram bot setup for non-technical users

Getting a bot token is a guided BotFather walkthrough with screenshots — mechanical, fine. **The chat
ID is the step that actually confuses people**, so automate it: the user clicks "Connect", presses
Start in their bot, and the platform calls `getUpdates` once and captures the chat ID itself. The
reference instance already has this logic in `pipeline/get_chat_id.py`; it becomes a form step
instead of a script.

## 3. Quotas — plan-scoped, enforced at admission

**One plan at launch: `free`** (decided 2026-07-26). Every user gets the same limits, exactly as the
brief asked.

But **store the limits on the plan, not in code constants**:

```sql
CREATE TABLE plans (
  id     TEXT PRIMARY KEY,        -- 'free'
  name   TEXT NOT NULL,
  limits JSONB NOT NULL           -- the table below
);
ALTER TABLE users ADD COLUMN plan_id TEXT NOT NULL DEFAULT 'free' REFERENCES plans(id);
```

That is the whole forward-compat move, and it costs nothing now: adding a paid tier later becomes
**inserting a row**, not a migration plus an audit of every hardcoded limit. Resolve quotas as
`users → plan_id → plans.limits`, with an optional per-user override column for support grants.

Do **not** build billing, payment, or upgrade flows yet — just the seam they will attach to.

| Quota | `free` limit | Enforced at |
|---|---|---|
| Search terms | 5 | Config save (no board is asked for them since 2026-11-04; the query set went 2026-11-06) |
| Notifications/day | 100 | `notify` worker, before send |
| CV/cover builds/day | 10 | `docgen` enqueue |
| Labels/day | 500 | `label` enqueue |
| Claude spend/day (API mode) | $2.00 | Pre-flight check before each call |
| Proxy MB/day (their own LinkedIn tier) | 200, **measure before fixing** | Skipped at cap (`HARVESTER_PLAN.md` §5.5) |
| User-triggered fetch MB/day | 50 | Fetch client wrapper; same credential, separate counter |
| Corpus queries/min (API) | 60 | API rate limiter |

**Enforce at the admission point** — the enqueue call or the API handler — not deep inside a worker.
A quota checked halfway through a job has already spent the resource it was protecting.

**All per-day counters reset at the user's local midnight** (`users.timezone`, IANA), not at UTC —
users are across Europe and internationally, so a UTC-only day is wrong from the first signup. Store
every timestamp as `TIMESTAMPTZ`; never naive.

Refuse loudly: over-quota returns an explicit error the UI shows ("daily CV limit reached, resets at
midnight your time"). **Never silently truncate.** Silent truncation is how a user concludes the product is
broken rather than that they hit a limit. Tested (T3).

**Global circuit breakers** sit above per-user quotas: total harvest queries/cycle, total proxy
MB/day, total platform Claude spend/day. When one trips, the platform degrades (skip the proxied
tier, defer the `label` lane) and alerts — it does not fail user-visible paths.

## 4. Config schema — bounded by construction

The requirement is that a non-technical user cannot produce an invalid or unrealistic config. Achieved
by making invalid states unrepresentable rather than by validating free text.

| Field | Type | Bounds | UI |
|---|---|---|---|
| `keywords` | list[str] | 1–5 items, 2–40 chars each | Tag input with counter |
| `exclude_keywords` | list[str] | 0–10 | Tag input |
| `locations` | list[enum] | 0–5, from a **controlled vocabulary** | Picklist (never free text — this is also what makes harvest dedup work, `HARVESTER_PLAN.md` §2) |
| `workplaces` | enum set | `remote`, `hybrid`, `onsite`; several are either, none is all | Switches |
| `seniority` | enum set | from a fixed list | Checkboxes |
| `min_salary` | int \| null | 0–500,000, currency from a picklist | Slider + "no minimum" |
| `languages` | enum set | ISO codes offered | Checkboxes |
| `timezone` | IANA string | validated against the tz database | Auto-detected from the browser, editable |
| `notify_interval_min` | int | **15–1440** | Slider with presets |
| `max_notifications_per_day` | int | 1–100 | Slider |
| `posted_within_hours` | int | 6–168 | Slider |

Rules:
- **Validate server-side with the same schema object the form renders from.** One Pydantic model,
  exported to the frontend as JSON Schema. Client validation is a convenience; the server is the
  authority.
- **Clamp nothing silently.** Out-of-range is a rejection with a message naming the field and the
  allowed range.
- **Live match count is the killer feature.** As the user edits, query the shared corpus:
  *"matches 1,240 jobs from the last 30 days"*. A config matching zero jobs is the most likely
  onboarding failure, and this makes it impossible to save one by accident. It is cheap **only**
  because the corpus is shared and already populated — another dividend of the §1 architecture.
- Warn (do not block) below a threshold: under ~150 matches, show "this is very narrow — you may get
  few results" with a widen suggestion (`COLD_START_PLAN.md` §7).

## 5. Authentication and onboarding order

Reuse the existing auth project as stated (`AUTH_REUSE.md`). Requirements it must meet:

- Password hashing: argon2id (or bcrypt ≥12 rounds).
- Email confirmation before any pipeline resource is provisioned — no unverified account consumes
  harvest budget or Claude spend.
- Sessions: httpOnly + Secure + SameSite cookies, or short-lived JWT with refresh rotation.
- Rate-limit login and password reset (these are the endpoints that get attacked).
- Generic failure messages — never reveal whether an email is registered.

### Onboarding order (decided 2026-07-26)

```
signup → verify email → free plan activated → credentials → profile capture → config → cold start
```

**Credentials precede capture** because the capture session runs on the user's own Claude credential
(`PROFILE_CAPTURE_PLAN.md` §4.1). Three things fall out of that ordering, all good:

1. No platform cost and no abuse surface on onboarding — a signup flood costs nothing.
2. Credentials are validated at the cheapest possible moment; a bad key fails at a form field rather
   than at 3 a.m. mid-backfill.
3. Nothing before email verification touches a paid resource.

The account is in `pending_verification` until the email is confirmed and `active` thereafter (see
lifecycle states, G11 in `GAPS_REVIEW.md`).

## 6. Account deletion

```sql
DELETE FROM users WHERE id = $1;   -- cascades every per-user table
```

Plus: overwrite the wrapped DEK, revoke sessions, delete generated documents from disk, and remove any
queued jobs (cascade handles the `jobs` table). **The shared corpus is untouched** — it never held
their data.

Confirm in a test that no orphan rows survive (T1 covers the read side; add a deletion assertion).

## 7. Abuse surface worth naming

| Vector | Mitigation |
|---|---|
| Signup flood consuming harvest budget | Email verification before provisioning; per-IP signup rate limit |
| Broad config triggering huge scrape | Term cap (5) + global harvest budget + round-robin fairness |
| Scripted API hammering | Per-user API rate limit; the corpus endpoints are the expensive ones |
| Credential stuffing on login | Rate limit + lockout + generic errors |
| Malicious job URL to `/cv <link>` | Already strictly validated `^https?://` in the reference instance — keep it; it prevents argument smuggling into the builder |
| A user's own bot spamming them | Per-user daily notification cap |
