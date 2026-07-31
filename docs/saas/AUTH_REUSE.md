# Auth & security — what to reuse from FlowSolutions

**Status:** review, 2026-07-26. Source: `d:\Projects\filip_project\flowsolutions-ai` (Go/Fiber,
MongoDB) — Ruben's own production codebase. Reviewed for what can be lifted into the job-search
platform.

---

## 1. What is actually there

`internal/auth/` is a complete, production auth system — roughly **3,900 lines** across:

| Area | Files | Notes |
|---|---|---|
| Password hashing | `v1/auth.go`, `v2/auth.go` | **argon2id**, per-secret random salt |
| JWT | `v2/jwt_gen.go`, `jwt_validate.go`, `keys.go`, `custom_tokens.go` | Generation, validation, key management |
| Sessions | `session/` (repository 987 lines, cache 334) | Refresh-token store, hashed refresh, in-process cache |
| MFA | `mfa/` (service 384, repository 338, `totp.go`, `recovery.go`) | TOTP + recovery codes, encrypted secret |
| Email verification | `verification/` (repository 271, `email.go`, `token_hash.go`) | Hashed tokens, success email |
| Cookies | `cookies.go` (197) | Flags, TTLs, path scoping |
| Rate limiting | `ratelimit/limiter.go` (67) | In-memory fixed window |
| OAuth | `googleoauth/`, `web/google_oauth.go` | Google sign-in |
| Secret encryption | `shared/secretcrypto/crypto.go` | **AES-256-GCM** |

Plus `internal/outboundmail` and `internal/notifications` for email delivery, and `internal/privacy`.

## 2. Can it be copied wholesale? No — and here is the honest reason

It is **Go + MongoDB + Fiber**, and it is application code rather than a library. The `internal/auth`
package imports FlowSolutions' own `leads`, `workflows`, `subscriptions`, `integrations`,
`google_sheets`, `users`, `app/config`, and `core/mongo/dbutil`. Extracting it means severing all of
that.

Against the platform decisions already made — **Python backend** (so the existing `pipeline/` and
`scorer/` code is reused unchanged) and **Postgres everywhere** (D1) — dropping in a Go/Mongo auth
service would reintroduce both a second language and a second datastore.

### The two real options

**Option A — standalone Go auth container.** Extract `internal/auth` + `secretcrypto` + `outboundmail`,
strip the FlowSolutions coupling, keep Mongo for auth only, ship it as its own container that the
Python `api` trusts via JWT.

- *For:* battle-tested code including MFA and session management; container topology already supports
  it; Ruben wrote it and knows it.
- *Against:* second language and second datastore, contradicting D1; ~3,900 LOC to decouple and then
  maintain; the coupling is deep enough that "extract" is closer to "rewrite with a reference".

**Option B — port the design to Python, reuse the decisions (RECOMMENDED).** Reimplement in FastAPI
using maintained libraries for the primitives, and copy the *policy* from FlowSolutions verbatim.

- *For:* one language, one datastore, matches D1; the security-critical primitives come from
  maintained libraries (`argon2-cffi`, `pyotp`, `cryptography`) rather than hand-rolled code; the
  decisions that took real time to get right are already made and are copied, not re-derived.
- *Against:* reimplementation of security-sensitive flows — mitigated by the fact that the hard parts
  are library calls plus the policy table in §3.

**Recommendation: B.** The value in FlowSolutions here is not the Go code — it is the ~15 decisions
below, each of which is easy to get subtly wrong and is already settled.

## 3. Decisions to copy verbatim

These are the payload. Each is a real choice already made correctly in production.

| Decision | FlowSolutions value | Python equivalent |
|---|---|---|
| Password hash | **argon2id**, `time=1`, `memory=64 MiB`, `threads=4`, `keylen=64`, `salt=32 bytes` | `argon2-cffi` `PasswordHasher(time_cost=1, memory_cost=65536, parallelism=4, hash_len=64, salt_len=32)` |
| Secret encryption | **AES-256-GCM**, 32-byte key from base64 env var, random nonce **prepended** to ciphertext, whole thing base64 | `cryptography` `AESGCM` — ~30 lines, same layout |
| Verification tokens | Generate with `crypto/rand` (hex), **store only the SHA-256 hash**, compare hashes | `secrets.token_hex()` + `hashlib.sha256` |
| Access token TTL | **15 minutes** | |
| Refresh token TTL | **30 days** | |
| Refresh token at rest | Stored **hashed**, never plaintext | |
| Cookie flags | `HTTPOnly` + `Secure` **always** | |
| Cookie SameSite | **`Lax` for access, `Strict` for refresh** | |
| Refresh cookie path | **Scoped to a narrow path** (`/api/refresh`), not `/` | The detail most implementations miss — it keeps the refresh cookie off every ordinary request |
| Password minimum | 8 characters | Consider raising; see §5 |
| Rate limiting | Fixed window, self-sweeping, keyed | See §4 for the caveat |
| MFA secret | Encrypted at rest with the same AES-GCM crypter | `pyotp` + the crypter |
| MFA recovery | Single-use recovery codes | |

**`secretcrypto/crypto.go` independently confirms `TENANT_SAFETY.md` §2.** The design specced there —
AES-256-GCM, 32-byte key held outside the database, nonce with the ciphertext — is exactly what is
already running in production. Port it directly; it is the reference implementation, and the platform's
per-user DEK layer wraps around it rather than replacing it.

## 4. Things to fix, not copy

Reviewing rather than lifting means noting what should not carry over:

1. **The rate limiter is process-local**, and its own comment says so ("intended as a first defense").
   With more than one `api` replica, each holds an independent bucket, so the effective limit is
   `limit × replicas`. Fine at ≤50 users on one replica; make it Postgres- or Redis-backed before
   scaling out, and do not treat it as an abuse control until then.
2. **8-character password minimum is low** for a product holding users' Claude API keys and proxy
   credentials. This platform shipped 12, then **settled at 9 on 2026-07-31** (owner's decision):
   12 was turning invited beta users away at the door, which costs more than the marginal entropy
   buys. A breached-password check (`zxcvbn` or HIBP k-anonymity) is still the cheap improvement
   worth having, and is worth more than another three characters — it is not implemented, because
   HIBP is a network call on the signup path and needs a timeout and a fail-open decision first.
3. **A 2-year cookie expiry** appears in `cookies.go` for a non-session cookie. Audit which cookie
   that is and whether the platform wants that lifetime.
4. **Two auth generations coexist** (`v1/` and `v2/`). Port only v2 and do not carry the v1 path over.

## 5. Also worth reviewing while in that codebase

| Package | Relevance |
|---|---|
| `internal/outboundmail` | The platform needs a verification-email sender (`GAPS_REVIEW.md` G3). Reuse the provider choice and templating approach |
| `internal/notifications` | Delivery patterns |
| `internal/privacy` | GDPR/deletion handling — directly relevant to `TENANT_SAFETY.md` §6 |
| `internal/subscriptions` | If billing ever happens, the model is already worked out |
| `internal/core/middleware/` | `allowed_hosts`, security headers |

## 6. Scope for v1

Not everything ships at launch. Suggested split:

- **Phase 3 (self-service launch):** password + argon2id, email verification, sessions with hashed
  refresh, cookie policy, rate limiting on login/reset, `secretcrypto` port. This is the minimum for
  holding strangers' credentials.
- **Phase 4 or later:** MFA/TOTP, Google OAuth sign-in. MFA is *more* justified here than in a typical
  product because the platform stores live API keys — but it is not launch-blocking for an invited
  cohort.
