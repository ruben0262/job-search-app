# embedder — Design Plan

*Lives at `job-search-app/services/embedder/docs/PLAN.md`. This is the maintained copy; the one in
`job-search/docs/saas/EMBEDDER_SHARING_PLAN.md` is the historical original and is not kept current.*

## 1. Problem

Every job-search deployment that scores postings locally (`SCORING_ENGINE=model`) needs to turn
job text into a vector before its trained classifier can use it. That text-to-vector step
(the "embedder") is:

- **Universal** — a pre-trained, general-purpose model (e.g. `BAAI/bge-base-en-v1.5`). It is not
  trained or customized by any individual job-search deployment; it is a fixed, frozen artifact
  downloaded from HuggingFace. Same input text -> same output vector, always, regardless of whose
  project is asking or what their trained classifier looks like.
- **Expensive to hold in memory** — ~1.4-1.9GB resident RAM once loaded (observed), because it is
  a real transformer network, not a lookup table.
- **Expensive to run** — every `/score` call is genuine neural-net inference (a forward pass per
  job posting's text), scaling with batch size and text length.
- **Duplicated today** — every deployment that scores locally loads its own private copy: once in
  its always-on serving container, and again (briefly) in its one-shot training container during
  a retrain. On a shared VPS running multiple people's job-search stacks side by side, this means
  N people each pay the full ~1.4-1.9GB RAM cost independently for byte-identical model weights.

**Origin:** this plan came out of a live incident (see `andras-job-search/docs/CHANGELOG.md`,
2026-07-24) where a shared VPS ran low on memory partly because of this duplication, causing the
serving container to die mid-request under load.

## 2. Goal

A single, shared embedding service that any job-search deployment (this user's own multiple
projects, and potentially other people's, if they opt in) can call over HTTP instead of loading
its own copy of the embedder. Each deployment keeps its own private, personalized classifier
(the actual "job fit" model) — only the universal text-to-vector step is centralized.

**Explicitly NOT a goal:** centralizing the classifier, the training data, or anything
personalized. Those stay private per deployment. Only the shared, non-personal, deterministic
embedding step is centralized.

## 3. Key design question: booking/lock vs concurrent vs batched

Three options were considered:

### Option A — Exclusive booking queue (one client at a time, others wait)
Rejected. Embedding inference is a **pure, stateless function** — it reads input text and
produces a vector; it does not mutate the model or any shared state. There is no correctness
reason to serialize callers. Locking would only add wall-clock latency: if client A's batch takes
2 minutes and client B's also takes 2 minutes, serializing makes it 4 minutes total instead of
letting both share the CPU concurrently in roughly the same total compute time.

### Option B — Naive full concurrency (every request processed immediately, no coordination)
Works correctness-wise (ONNX Runtime, which `fastembed` uses under the hood, is thread-safe for
concurrent inference calls) but can cause CPU thrashing if many small requests arrive at once —
many tiny inference calls are less efficient than fewer, larger batched calls, and uncoordinated
concurrent load on a CPU-bound service can degrade throughput for everyone.

### Option C — Dynamic (continuous) batching — CHOSEN
The production-standard pattern (used by NVIDIA Triton, HuggingFace's own embedding server,
BentoML, TorchServe): a short internal queue that accumulates whichever requests arrive within a
small time window (tens of milliseconds — NOT minutes) or until a batch-size cap is hit,
whichever comes first, then processes them as one batched `embed()` call and returns each
caller's slice of the result. This gets:
- No thrashing from true simultaneous heavy calls (bounded by the batch window/cap).
- Near-zero added latency for any single caller (the window is milliseconds, not a real queue
  wait).
- Better throughput than either A or naive B, because batched inference is more CPU-efficient
  per item than one-at-a-time calls.

This is the technically correct version of the "booking" instinct that started this discussion:
coordinate access, but at the millisecond/batch-size scale, not the whole-request scale.

## 4. Build vs adopt

Two implementation paths, both viable:

### 4a. Custom lightweight service
A small FastAPI (or similar) app wrapping `fastembed.TextEmbedding`, with a `POST /embed`
endpoint and a hand-rolled dynamic-batching queue (a bounded asyncio queue + a background task
that drains it every N milliseconds or at a size cap). Full control, minimal dependencies, easy
to reason about and modify. More code to write and maintain (the batching logic itself, health
checks, metrics).

### 4b. Adopt HuggingFace Text Embeddings Inference (TEI)
`https://github.com/huggingface/text-embeddings-inference` — a purpose-built, production-grade
embedding server (Rust), with continuous/dynamic batching, health checks, Prometheus metrics, and
direct support for BAAI/bge models (and most other HuggingFace embedding models) already built
in. Pros: battle-tested, fast (Rust, no Python GIL contention), zero custom batching code to
write or maintain. Cons: another piece of third-party infrastructure to run and keep updated; a
Docker image dependency rather than pure-Python; less flexibility if a future need arises that
TEI doesn't cover (e.g. a custom pre/post-processing step per caller).

**Recommendation:** prototype with 4b (TEI) first — it directly solves the batching problem
without writing/maintaining that logic, and standing it up is a docker-compose service, not new
code. Fall back to 4a only if TEI's model support or request/response shape doesn't fit
(`fastembed`'s output format is close to TEI's `/embed` endpoint, so client-side changes should
be minimal either way).

## 4c. Batching optimization — the concrete knobs (Triton/TEI dynamic batching)

Option C (§3) is the right pattern; this pins the actual production knobs, because a central embedder
serving **both real-time scoring and bulk training for multiple users** must be fast *and* correct.

### Core knobs (Triton names / TEI equivalents)

| Purpose | Triton (`dynamic_batching`) | TEI flag | Effect |
|---|---|---|---|
| Target batch to dispatch | `preferred_batch_size` | `--max-batch-tokens` (token-based) | Dispatch when this size/token-count is hit. |
| Latency⇄throughput dial | `max_queue_delay_microseconds` | internal queue window | Wait up to this long to fill a batch; **no request waits longer**. |
| Bound the queue | `max_queue_size` | `--max-concurrent-requests` | Backpressure vs unbounded memory growth. *(gateway: `EMBEDDER_MAX_QUEUE` → 503.)* |
| Priority lanes | `priority_levels` | (front a small priority queue) | Higher priority **bypasses** lower. |

Dispatch fires when **either** a preferred size is reached **or** the delay expires — the dynamic-batching contract.

### Decisive nuance: scoring and training want OPPOSITE settings

| Workload | Goal | Batch | Queue delay |
|---|---|---|---|
| **On-arrival scoring** (user waiting) | **low latency** | small, dispatch fast | **tiny** (~5–10 ms) |
| **Training** (bulk, offline, cache-fed) | **throughput** | large, saturate CPU | **larger** (~50–100 ms / fill to token cap) |

**Solution — two lanes on one instance:** a high-priority **score** lane (tiny delay, small batch,
bypasses a training flood) and a bulk **train** lane (yields to score). Callers tag the lane. This is
Triton's `priority_levels`; with TEI it is approximated in a gateway in front of it
(`gateway/dispatcher.py`). Prefer one instance + a gateway over two TEI containers, which would double
RAM and fight the <2GB budget.

⚠ **Ordering alone does not deliver this** (measured, §15): a priority queue picks who goes *next*, but
a score request still waits out whatever bulk call is already in flight. The fast lane needs **reserved
workers**, and bulk chunks small enough that the backend frees up quickly — on a CPU backend whose
internal batch caps at 8, "fat batches" for the bulk lane is actively wrong: it buys no throughput and
only lengthens the block.

### Padding / length bucketing (free speed, no accuracy cost)

A batch pads every sequence to its longest, so mixing a 20-token title with a 2000-token JD wastes
compute. Sort/bucket by length within a batch (TEI's token batcher does this) — a throughput win that
**changes no vector**.

### CPU + <2GB — what NOT to copy from NVIDIA

- **No concurrent model execution / `instance_group` > 1** (multiple resident copies) — a RAM
  multiplier. One instance; parallelism from dynamic batching + threads.
- **Cap `max_batch_tokens`** — a giant batch spikes the onnxruntime working set past 2GB. The token cap
  *is* the memory cap (§12).

### Accuracy invariant

Embeddings are **per-sequence independent** (no cross-sequence attention), so a correctly masked batch
gives the **same vector** whether a text is alone or grouped. Batching is speed-only, never an accuracy
trade — *if* padding is masked right. Guarded by the batch-invariance test (§14).

## 5. Architecture

```
                     ┌─────────────────────────────┐
                     │   job-search-embedder        │
                     │  (TEI or custom FastAPI)      │
                     │  loads BAAI/bge-* ONCE        │
                     │  POST /embed  {texts: [...]}  │
                     │       -> {vectors: [[...]]}   │
                     └───────────────┬───────────────┘
                                     │  shared Docker network
                                     │  (external: true)
              ┌──────────────────────┼──────────────────────┐
              │                      │                      │
    ┌─────────▼─────────┐  ┌─────────▼─────────┐  ┌─────────▼─────────┐
    │ andras-job-search  │  │  (other project)  │  │  (other project)  │
    │  scorer-service     │  │  scorer-service   │  │  scorer-service   │
    │  (own classifier)   │  │  (own classifier) │  │  (own classifier) │
    │  scorer (training)  │  │       ...         │  │       ...         │
    └────────────────────┘  └───────────────────┘  └───────────────────┘
```

Each project keeps:
- its own trained classifier (`scorer.joblib` equivalent) — fully private
- its own `scorer-service`-style thin wrapper that calls out to the shared embedder for vectors,
  then runs its own classifier on the result

The shared embedder keeps:
- no per-project state, no classifiers, no labels — purely `text -> vector`
- one loaded copy of the model, serving all callers

## 6. Multi-tenancy: same-project vs cross-project

Two deployment scopes, deliberately kept separate in this plan:

- **Same-project sharing (this user's own deployments across `andras-job-search`'s `scorer` and
  `scorer-service`)**: fully actionable unilaterally — no coordination needed, no trust boundary
  crossed. This is the first, safe rollout target.
- **Cross-project sharing (with another, independently-run job-search deployment on the same
  VPS)**: requires the other deployment's own code to change (to call the shared embedder instead
  of loading its own), and introduces a real coupling — if the shared embedder has a problem, ALL
  attached projects lose scoring simultaneously, not just one. This should only be pursued with
  the other project's explicit, informed buy-in, never silently. Docker supports this via an
  `external: true` network that independent compose projects can both attach to, but the
  ownership/reliability trade-off is a joint decision, not a unilateral one.

**Recommended rollout order:** ship same-project sharing first (real, low-risk, fully in this
user's control), prove it out, THEN decide separately whether to offer it cross-project.

## 7. API shape (sketch)

```
POST /embed
{
  "texts": ["job title\njob description text...", "..."]
}
->
{
  "vectors": [[0.0123, -0.045, ...], [...]],
  "model": "BAAI/bge-base-en-v1.5",
  "dim": 768
}

GET /health
-> {"ok": true, "model": "BAAI/bge-base-en-v1.5", "dim": 768}
```

Callers must send the SAME text-construction logic each project already uses (title + description,
truncated at the same length) — the embedder does not know or care about a project's specific
`build_text` conventions; that logic stays on the CALLER side, unchanged. The embedder only turns
whatever text string it's given into a vector.

## 8. Open questions — all closed (audited 2026-07-31)

The service is built and deployed on both boxes; this section is kept for the record rather than
as work outstanding.

1. ~~Which embedding model(s) need serving — one or multi-model?~~ **Settled (§10, 2026-07-25):**
   one model, **bge-base (768d)**, every tenant retrains onto it. No multi-model serving.
2. ~~Decide the batching window and max batch size empirically.~~ **Tuned and in
   `deploy/compose.embedder.yml`:** `--max-batch-tokens=16384`, `--max-concurrent-requests=512`,
   `--max-client-batch-size=32` with `--auto-truncate`. The client batch size feeds the gateway's
   chunk size from the same variable, so the two cannot drift into a 413.
3. ~~Decide where the service runs and how consumers point at it.~~ **Two deployments, on
   purpose** (D6, parallel until the platform provably matches): one on the platform box, one on
   the legacy box still serving `job-search` and `andras-job-search`. Consumers use `EMBEDDER_URL`
   over the local `embedder-net`; a cross-host arrangement would need an auth token and TLS for no
   benefit, since vectors are a deterministic function of the text.
4. ~~Migration plan for `andras-job-search`.~~ Moot for this repository — that is the legacy
   single-tenant instance on the other box, and the platform is the consumer that matters here.
   The platform's own path is `libs/contracts/embedder.py` plus the harvester's embed step.
5. ~~Revisit exposing this to another tenant's stack.~~ Answered by the platform existing: it *is*
   the multi-tenant consumer, and every user's jobs are embedded through one shared service.

**What is live and worth knowing instead**, because it is what actually bites: the embedding text
mode is part of `EmbedderVersion.version`, and it changed from `desc` to `title_desc` on
2026-07-31. Every vector written before that is refused rather than silently mis-scored, which is
why a full re-embed is in progress — see `CHANGELOG.md` and `docs/saas/HANDOVER.md`.

## 9. Current wiring on the consumer side (verified against the job-search code, 2026-07-24)

How a consumer deployment uses the embedder today, so the extraction is surgical:

- **Primary scoring is the local model, NOT Haiku.** Consumers default `SCORING_ENGINE=model`; without
  the embedder the free Phase-2 path is gone and scoring falls back to paying an LLM per job.
- **Serving:** the embedder loads **in-process inside the `scorer-service` container** —
  `scorer/score.py` `TextEmbedding(...)`, resident ~1.9GB. Flow is `text → embed → hstack(features) →
  model.predict`. Pipeline + watcher already POST to `scorer-service` over HTTP (the service boundary
  exists at the *scorer*; this plan pushes it one layer down to the *embedder*).
- **Training:** the `scorer` trainer loads its **own** embedder copy (`scorer/train.py`), embedding via a
  cached `_embed` — the **double-copy** that a shared embedder removes.
- **Embed cache:** persistent on-disk, keyed by `md5(text)` per model; a retrain embeds **only new rows**
  since the last one. Cold start (first train / wiped cache / new model) is the only full-corpus embed.

**Extraction seam:** replace the in-process `TextEmbedding` in `score.py` (serving → fast lane) and
`train.py::_embed` (training → bulk lane) with the HTTP client; keep `features.py` + `model.predict`
local. Only the universal `text → vector` step goes remote.

## 10. ⚠ Model mismatch between the two live instances — resolve before sharing

The two instances that would share the embedder were **not on the same BGE model** (verified 2026-07-24):

| Instance | Model | Dim | Source |
|---|---|---|---|
| **job-search** | `BAAI/bge-small-en-v1.5` | **384** | no `.env` override → train default |
| **andras-job-search** | `BAAI/bge-base-en-v1.5` | **768** | explicit `SCORER_EMBED_MODEL` in `.env` |

384 and 768 are **different vector spaces**; one shared embedder serves one model → one dim, so it cannot
feed both classifiers as-is (this answers open question §8.1). **Resolution:** pick ONE shared model; the
off-model instance **retrains** its classifier on it (the sklearn head's input width is fixed by the dim —
retrain is mandatory, and the cache cold-embeds once on the new model).

| Shared model | RAM | Quality | Who retrains | Fits <2GB? |
|---|---|---|---|---|
| **bge-small (384d)** | 282 MB *(measured)* | good, CPU-fast | andras retrains down | yes |
| **bge-base (768d)** | ~2× → still far under | better | job-search retrains up | **yes** |

### DECIDED 2026-07-25: standardise on **bge-base (768d)**

The original recommendation was bge-small, and its stated reason was the <2GB budget. Measurement
killed that reason: bge-small peaked at **282 MB** (§12), so bge-base at roughly twice that is still
an order of magnitude inside the ceiling. The 1.9GB figure that drove the whole "small or bust"
instinct was the *whole scorer-service container*, never the embedder alone.

With RAM off the table the decision is purely fit quality, and bge-base is the stronger model — so it
wins by default rather than needing an A/B to justify it. **The cost is CPU, not memory:** bge-base is
~3× the compute per text, so expect the §12 throughput and score-latency numbers to degrade roughly
proportionally, and re-tune `cpus` / worker pools from a fresh measurement rather than assuming the
bge-small numbers carry over.

**Every attached classifier retrains** — both tenants, since job-search was on small (384d) and andras
had already been retrained onto small. Vectors from a 384d space are not convertible to 768d; there is
no migration path that skips retraining. The embed cache is keyed per model, so each tenant
cold-embeds its corpus once on the new model. Cross-instance decision (§6).

## 11. Training placement — VPS-via-shared-embedder (default) vs local-and-upload (fallback)

The shared embedder removes the reason to avoid VPS training: the trainer no longer loads a second
embedder copy — it just `POST /embed/bulk`, RAM becomes just sklearn.

| Option | Status | Notes |
|---|---|---|
| **B) Train on VPS via the shared embedder** | **Recommended default** | Double-copy gone; caller-side cache keeps retrains incremental. |
| **A) Train locally, upload `scorer.joblib`** | **Fallback** | Artifact is portable (records `embed_model`); good for a heavy cold-start on a dev box. |

Embed cache stays **caller-side** (labels differ per instance; embedder stays stateless). Optional later:
a *shared* embed cache on the service (same text → same vector for everyone). Both placements require the
same model+version the classifier was trained on (§13).

## 12. Load & memory + the <2GB conditions

2–3 instances, scoring within a ~25-min window, usually not simultaneous, occasionally overlapping on
training. Serving is trivial (~3 jobs/min, sub-second on bge-small); incremental training is light (cache
→ tens of docs); the **only spike is a cold-start full-corpus embed** (CPU-time, not a RAM blowup, since
batches are bounded). onnxruntime is thread-safe; the dynamic-batching queue merges 2–3 overlapping
callers.

**<2GB is driven by batch size, not caller count** — micro-batches cap the working set. Achievable **only
if**: (1) an **embedder-only image**; (2) **bounded `max_batch_tokens`** so training streams as
micro-batches. **Measure peak RSS on the box — do not promise it.**

### Measured on the VPS, 2026-07-25 (bge-small, chunk 8, 3 concurrent scorers + a 2000-text cold train)

| Metric | Result |
|---|---|
| **Peak RSS (tei container)** | **282 MB** vs a 2048 MB budget |
| Bulk cold-start throughput | 2000 texts in 303s (~6.6 texts/s, 1 bulk worker) |
| Score latency, idle | 0.08s |
| Score latency under a full cold-train flood | median 1.50s, p95 2.81s, max 4.90s (n=512) |
| Errors | 0 |

*(These are **bge-small** numbers, taken before the §10 switch to bge-base. bge-base is ~3× the
compute per text, so re-run `scripts/load_rss.py` on base and replace this table — do not carry these
figures forward.)*

**The RAM fear was wrong by ~7×.** The ~1.9GB figure in §1/§9 was the whole `scorer-service`
container (fastembed + onnxruntime + sklearn + FastAPI + numpy); an embedder-only TEI is a fraction of
it. Consequences: (a) the <2GB budget is not a binding constraint and should stop being cited as one;
(b) **it no longer justifies choosing bge-small over bge-base** (§10) — bge-base at ~2× is still far
inside budget, so that choice is now purely a fit-quality question, to be settled by the A/B, not by
memory; (c) the real scarce resource is **CPU**, not RAM — throughput and score latency under load are
what to tune, and the levers are worker pools and chunk size (§4c), not the token cap.

## 13. Version-pinning correctness constraint (must-do)

A classifier is bound to the exact vectors it trained on; a silent embedder drift degrades it with **no
error**. Guardrails:
- `/health` exposes `{model, dim, version}`. Each caller **asserts** its artifact's model+dim match
  **before trusting any score**; on mismatch it **refuses (loud)** (implemented in the consumer client's
  `assert_compatible`).
- **The service must verify its own claims.** A caller's assert is only as good as `/health`, so the
  gateway probes the live backend at startup (`/info.model_id` + the width of a real embed) and marks
  itself `verified` only when they match its config; a mismatch is terminal (`ok: false`, 503 on
  embeds) so a `.env` drift cannot pass an assert with wrong vectors (`gateway/verify.py`).
- `build_text` + truncation stay **identical caller-side** — the embedder only vectorizes the string it
  is handed.
- **Pin the model/`fastembed`/TEI version** on the service; a bump that changes vectors is a **coordinated
  migration** (everyone retrains), like a rubric change.

## 14. Pre-deploy test plan (prove it in reality first)

Nothing switches until these pass (skeleton stubs live in `tests/`):
1. **Vector parity** — remote `/embed` == the old in-process `fastembed` vector (within tolerance) for a
   fixed model+version. The gate; if vectors match, classifiers are unaffected. *(If they differ: match
   TEI's pooling/normalisation, or adopt TEI as source of truth and retrain — which §10 forces anyway.)*
2. **Version/dim assertion** — caller refuses to score on a `/health` mismatch.
3. **Score equivalence** end-to-end — remote-embedder path == in-process path on a frozen job set.
4. **Cache incrementality** — second retrain embeds ~0 new (cache hit) through the HTTP boundary.
5. **Load + memory** — 3 concurrent callers + a cold-train burst; **peak RSS < 2GB** and acceptable latency.
6. **Failure blast-radius** — embedder down → callers **fail closed** to "cannot score", not garbage.
7. **Batch-invariance** (accuracy, §4c) — same text alone == inside a large mixed batch (identical vector).
8. **Priority-lane latency** — under a training flood, a single score request still returns within target
   (proves the score lane bypasses the bulk lane).

## 15. Status

Skeleton built (TEI + thin FastAPI gateway with two priority lanes, network-only, **bge-base pinned**
as of 2026-07-25 — see §10).
**Consumer side wired in job-search** behind `EMBEDDER_URL` (unset = in-process fastembed, unchanged
default): `scorer/embedder_client.py` (client + `assert_compatible` + fast/bulk lanes), `score.py` (fast
lane), `train.py` (bulk lane), and `docker-compose.yml` (attached to `embedder-net`).

**Closed since the first skeleton (2026-07-25):**
- **§13 enforced server-side** (`gateway/verify.py`). `/health.ok` was previously just TEI's liveness
  plus whatever `.env` claimed — so an `EMBEDDER_MODEL`/`EMBEDDER_DIM` that drifted from the model TEI
  actually loaded would satisfy every caller's `assert_compatible` and produce silently wrong scores,
  the precise failure §13 exists to prevent. The gateway now probes the live backend at startup
  (`/info.model_id` + a real embed's width) and refuses to report healthy — permanently, 503 on both
  embed endpoints — on a mismatch.
- **§4c "bound the queue"** — the dispatch queue is bounded (`EMBEDDER_MAX_QUEUE`, default 256) and
  sheds load with 503 rather than growing memory while callers time out anyway.
- **Per-call batch ceiling** (found on first VPS boot). TEI 413s any single call above its
  `--max-client-batch-size` (default **32**), while the gateway was forwarding bulk chunks of 64 and
  never chunked the fast lane at all — so every training embed and any multi-posting score request
  would have failed. One env (`TEI_MAX_CLIENT_BATCH`) now feeds both TEI's flag and the gateway's
  chunk ceiling, both lanes chunk, and the gateway clamps to whatever `/info` actually reports.
- **§14 coverage** — §14.2 (`test_version_assert`), §14.6 (`test_fail_closed`), §14.8
  (`test_priority_lane`), §14.5 harness (`scripts/load_rss.py`), plus a docker-free gateway-logic
  suite (`test_gateway_contract`: mismatch refusal, fail-closed, bulk ordering, caps, auth).
  §14.3/§14.4 belong consumer-side (they need a classifier + its cache); map in `tests/README.md`.

- **§14.8 lane starvation — the priority queue was not sufficient** (measured on the VPS: score
  latency 0.08s idle → **8-15s** under a bulk flood, budget 5s). Priority decides who is picked up
  *next*; it cannot recall a worker already blocked inside a multi-second TEI call, so with a shared
  pool a score item sat at the head of the queue and waited anyway (head-of-line blocking). Fixed
  with **separate worker pools per lane** (`EMBEDDER_SCORE_WORKERS=2`, `EMBEDDER_BULK_WORKERS=1`)
  plus a bulk chunk of **8** — TEI's CPU backend caps its internal batch at 8, so larger chunks buy
  no inference speed and only widen the window in which the backend is unavailable. This is the
  correction to §4c's claim that a gateway priority queue approximates Triton's `priority_levels`:
  it does so only if the high-priority lane has reserved capacity.

**Verified on the VPS 2026-07-25:** stack boots, `/health` backend-verified, §14.2 / §14.6 / §14.7
pass, §14.5 peak RSS **282 MB** (numbers in §12), §14.8 passes at a 2s budget after the two-pool fix.

**Still open:** **§14.1 parity has never actually run** (it skips unless `fastembed` is installed) —
it is the gate that decides whether existing classifiers keep working or must be retrained, so no
consumer should be switched over before it does. `EMBEDDER_MAX_QUEUE` raised to 2048 because one
2000-text retrain alone enqueues 250 chunks and the old bound of 256 would have 503'd a second
tenant's concurrent retrain. andras-job-search is still on in-process `fastembed` with no
`embedder-net` wiring.
Key decisions: **standardise on bge-base (768d)** — both tenants retrain (§10); default to VPS
training via the shared embedder (§11); RAM is a non-issue, CPU is the constraint (§12); enforce
version pinning (§13); pass the test plan (§14) before any switch.
