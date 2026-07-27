# Operations — monitoring, email, backup, recovery

**Status:** design, 2026-07-26. Closes gaps **G2** (observability), **G3** (transactional email),
**G4** (backup/restore), **G10** (embedder SPOF) from `GAPS_REVIEW.md`. Blocks Phase 4.

---

## 1. The problem: every failure here is silent

A job feed that stops looks exactly like a quiet week in the job market. That single fact drives this
whole document — the user cannot distinguish a broken pipeline from a slow market, so they will not
report it. They will just leave.

Failures that produce no signal today:

| Failure | What the user sees | What they conclude |
|---|---|---|
| Proxy credential quarantined | LinkedIn jobs stop | "fewer jobs this week" |
| Telegram bot blocked / token revoked | Nothing at all | "it stopped working" |
| Training job dead-lettered | Scores stay stale | nothing — invisible |
| Embedder unverified | No new scores | "no matches" |
| Harvest budget exhausted | Fewer jobs | "quiet week" |
| Config matches ~nothing | Empty feed | "there are no jobs for me" |

"Ruben reads the logs" is the current monitoring design. It does not survive five users, and it
certainly does not survive the failure modes above, none of which produce an error anyone is looking
at.

**Principle: every silent failure gets both an alert to the operator and a message to the affected
user.** Telling only the operator leaves the user guessing; telling only the user makes them the
monitoring system.

## 2. Per-user health

One row per user, computed on demand, surfaced on their dashboard and in the admin view:

```
last_harvest_at            last successful shared-harvest cycle (platform-wide)
last_linkedin_fetch_at     their per-user LinkedIn loop (D3)
last_delivery_at           last Telegram message actually accepted
jobs_delivered_24h
model_version, model_trained_at, labels_count
queue_position             pending jobs in each lane
proxy_status               healthy | quarantined | over_cap | absent
claude_status              ok | invalid | over_cap ; spend_today
telegram_status            ok | broken
config_match_estimate      matching corpus jobs in the last 30 days
stage                      rules_only | bootstrap | personal   (COLD_START_PLAN §2)
```

`config_match_estimate` is the one that pre-empts the most common support question. If it reads 4,
the user's filters are the problem, and showing that beats any error message.

## 3. Platform health

```
queue_depth{lane}          notify | train | docgen | label
dead_letter_count{lane}
oldest_ready_job_age{lane}
embedder_verified          bool  + queue depth
harvest_queries_used / budget
corpus_rows, corpus_bytes  vs the PLATFORM_ARCHITECTURE §4 projection
postgres_size, connection_count
proxy_mb_today{user}
claude_spend_today{user}
```

**Track corpus growth against the projection explicitly.** The §4 sizing (0.87 GB steady state)
assumes the 90-day retention trim actually runs. If growth tracks above projection, the trim is
broken — and that is a slow, silent failure that only shows up as a full disk months later.

## 4. Alerts

Alert on **conditions that need a human**, not on every error. Everything else is a dashboard number.

| Condition | Severity | Action |
|---|---|---|
| Any job dead-lettered | 🔴 page | Bug or a broken user credential; both need looking at |
| A user with no successful delivery in 24 h **while jobs matched** | 🔴 page | The silent-feed failure |
| Embedder `verified=false` > 5 min | 🔴 page | Nobody is being scored |
| Postgres disk > 80% | 🔴 page | |
| `oldest_ready_job_age` > 4× lane's expected | 🟠 warn | Queue stalling |
| Corpus growth > 1.5× projection over 7 days | 🟠 warn | Retention trim broken |
| Harvest budget exhausted 3 cycles running | 🟠 warn | Under-provisioned or a runaway config |
| A user's proxy quarantined | 🟠 warn + **notify the user** | |
| A user's Claude credential invalid | 🟠 warn + **notify the user** | |
| Telegram `broken` | 🟠 warn + **notify the user** | G6 |

The "no delivery in 24 h **while jobs matched**" qualifier matters — without it the alert fires
constantly for users with narrow configs, and an alert that cries wolf is worse than none.

### 4b. Telling the user — email + in-app banner (decided 2026-07-26)

**Telegram is the product feed, not the ops channel.** Platform problems reach the user by **email**
and a **persistent in-app banner**, never through the job feed.

That separation is deliberate and it fixes a real circularity: one of the most likely failures *is*
the Telegram connection, and a channel cannot report its own death. Email always survives it. It also
keeps the feed clean — a user scanning job alerts should not have to filter platform notices out of
it.

| Condition | Banner | Email | User can self-serve? |
|---|---|---|---|
| Claude credential invalid | ✅ persistent | ✅ | Yes — re-enter the key |
| Claude spend cap reached | ✅ until reset | ✅ once | Yes — wait, or raise it |
| Proxy credential failing | ✅ persistent | ✅ | Yes — re-enter |
| Proxy daily MB cap reached | ✅ until reset | — | Yes — wait |
| Telegram bot disconnected | ✅ persistent | ✅ | Yes — reconnect |
| Config matches ~nothing | ✅ persistent | — | Yes — widen filters |
| Cold start finished | ✅ dismissible | ✅ | n/a |
| Profile capture failed | ✅ persistent | ✅ | Yes — retry or enter manually |
| Platform incident | ✅ dismissible | only if >1 h | No — operator fixes it |

Three rules that keep this from becoming noise:

1. **Every banner says what to do**, not just what broke. "Your proxy credential is failing —
   LinkedIn results have paused. Re-enter it in Settings." Never a bare error code.
2. **Banner state is derived, not stored.** It is computed from the same health fields as §2, so it
   clears itself the moment the underlying condition clears. A banner that needs manual dismissal to
   disappear will outlive its cause.
3. **One email per condition per occurrence**, not per retry. A flapping credential must not email
   someone hourly (`TENANT_SAFETY.md` §3 rate limits).

**Implementation:** Prometheus metrics (the reference instance already instruments with Prometheus)
+ Alertmanager, or a plain scheduled checker writing to a `platform_alerts` table and emailing. At
this scale the simple version is defensible; do not build an observability platform.

## 5. Admin console — action-capable, fully audited

**Decided 2026-07-26: the operator fixes problems from the dashboard, not from a terminal.** Admin can
act, every action is audited, and impersonation stays off.

### Read

- User list with the health columns from §2, sortable and filterable by unhealthy state.
- One user's detail: config, queue state, recent deliveries, last errors, quota usage, credential
  states (**never values** — `TENANT_SAFETY.md` §2's write-only rule holds here too, so `last_4` and
  status only).
- Queue browser with dead-letter inspection.
- Corpus stats and per-source harvest yields.

### Act

| Action | Reversible | Notes |
|---|---|---|
| Requeue a dead-lettered job | ✅ | The most common fix |
| Kill a stuck job | ✅ | Returns it to the queue or dead-letters it |
| Force a retrain | ✅ | Bypasses the debounce |
| Resend the last notification batch | ✅ | Dedup still applies, so it cannot double-send |
| Reset a per-day quota counter | ✅ | For a user wrongly capped by a bug |
| Un-quarantine a proxy credential | ✅ | After the user confirms they fixed it |
| Clear the Telegram `broken` flag | ✅ | After a reconnect |
| Pause / resume a user's pipeline | ✅ | |
| Edit a user's config | ⚠️ | Same validation as the user's own form — no bypass |
| Suspend an account | ⚠️ | Abuse response |
| Delete an account | ❌ | Two-step confirmation; cascades per `TENANT_SAFETY.md` §6 |

### Audit

```sql
CREATE TABLE admin_audit (
  id           BIGSERIAL PRIMARY KEY,
  actor_id     UUID NOT NULL REFERENCES users(id),
  action       TEXT NOT NULL,
  target_user  UUID REFERENCES users(id) ON DELETE SET NULL,
  target_ref   TEXT,                    -- job id, credential id, ...
  before       JSONB,
  after        JSONB,
  reason       TEXT,
  at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Every action writes a row — **reads of another user's data included**, because looking at someone's
job search should leave a trace even when it is entirely legitimate. `target_user` is
`ON DELETE SET NULL` rather than `CASCADE`: deleting a user must not erase the record that an
operator acted on their account.

### Not built

**No impersonation.** These accounts hold live Claude API keys and proxy credentials; acting *as* a
user is a liability that the action list above does not require. Everything support genuinely needs is
either a read or a named action.

Admin access is gated to operator accounts and should require MFA once `AUTH_REUSE.md` §6 lands.

## 6. Transactional email (G3) — now load-bearing

Email is no longer just signup verification. Under §4b it is **the only channel that survives a broken
Telegram connection**, which makes it a launch dependency rather than a nicety.

Needed for: signup verification, password reset, proxy failing, Claude credential invalid, Telegram
broken, quota exhausted, cold start finished, profile capture failed.

- **Provider:** Postmark or SES. Reuse the approach in FlowSolutions' `internal/outboundmail`
  (`AUTH_REUSE.md` §5) rather than deriving it again.
- **Deliverability is the real work**, not the sending: SPF, DKIM, DMARC on the sending domain, plus
  bounce and complaint handling. A verification email in spam is an onboarding funnel that silently
  leaks users.
- **Templates:** plain, text-first. These are operational messages, not marketing.
- **Bounce handling:** a hard bounce marks the address unverified and suspends the account
  (`TENANT_SAFETY.md` lifecycle states, G11).
- **Rate limit** per user per type so a flapping credential cannot email someone hourly.

## 7. Backup and restore (G4)

Postgres is now the single point of failure (`PLATFORM_ARCHITECTURE.md` §10.3), and under the revised
**D4 it is self-managed** — no provider is taking backups, PITR, or failover off your hands. This
section is full scope, not a verification of someone else's work.

Two specific ways to get it catastrophically wrong, both silent until the day they matter:

1. **KEK backed up *with* the database** → the backup is a complete credential breach in one file.
2. **KEK not backed up *at all*** → a restore yields a database of undecryptable credentials, and
   every user must re-enter every secret.

The KEK must be backed up, **separately, to a different location with different access control.**

| What | How | Where | Retention |
|---|---|---|---|
| Postgres | Nightly `pg_dump` + WAL archiving | **EU** off-box object storage, encrypted | 30 days |
| KEK | Manual, at rotation | Separate secret store, different credentials | Forever, versioned |
| Model artifacts | Nightly with the DB | Same as DB | 7 days (regenerable) |
| Corpus | Covered by the DB dump | | |

**Recovery objectives:** RPO 24 h (one night of harvest lost — recoverable, the sources are still
there), RTO 4 h.

**Restore drill before launch and quarterly after.** Restore into a scratch environment, verify a
user's secrets decrypt, verify the scorer serves. An untested backup is not a backup, and the
KEK-separation design above is exactly the kind of thing that is only proven by a real restore.

## 8. Embedder as second SPOF (G10)

Fail-closed is correct and already implemented — scoring against a mismatched vector space would be
silent garbage, which is worse than no score. But if the embedder is down, nobody gets scored.

Accept it for v1, and make the consequences bounded:

1. **Alert on `verified=false`** (§4) — it is currently visible only to a caller that happens to try.
2. **Harvest keeps running and keeps queuing unembedded jobs**, so recovery is a drain rather than a
   gap. **Verify this is actually what happens** — if the harvester instead drops jobs it cannot
   embed, an embedder outage becomes permanent data loss, and that is a materially different bug.
3. **Vectors are cached on corpus rows**, so an outage never affects already-embedded jobs. Existing
   feeds keep scoring; only new arrivals stall.
4. Do **not** add a fallback embedder. A second model means a second vector space, which is the exact
   failure the version assert exists to prevent.

## 9. Runbooks

Short, written before they are needed:

| Situation | Runbook |
|---|---|
| Embedder unverified | Check TEI health → model/dim/version vs artifacts → restart → verify drain |
| Queue stalled | Check dead letters → inspect `last_error` → fix or requeue → check worker liveness |
| Postgres growing off-projection | Check retention trim ran → check for un-trimmed descriptions → verify pinning logic |
| A user reports no jobs | Check `config_match_estimate` first, then credentials, then queue |
| Proxy quarantined | Confirm with the user; their credential, not the platform's |
| Restore | The §7 drill, written as steps |

## 10. What this deliberately does not include

- Distributed tracing, service mesh, an observability vendor. Scale does not justify them.
- Uptime SLAs. Not a paid product yet, and promising availability changes the operational bar.
- On-call rotation. One operator; alerts go to one place.
