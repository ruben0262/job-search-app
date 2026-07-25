# job-search-embedder — Design Plan

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

**Solution — two priority lanes on one instance:** a high-priority **score** lane (tiny delay, small
batch, bypasses a training flood) and a bulk **train** lane (fat batches, yields to score). Callers tag
the lane. This is Triton's `priority_levels`; with TEI it is approximated by a small priority queue in a
gateway in front of it (the skeleton's `gateway/dispatcher.py`). Prefer one instance + a priority queue
over two TEI containers, which would double RAM and fight the <2GB budget.

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

## 8. Open questions / next steps before implementation

1. Confirm which embedding model(s) need serving — if `andras-job-search` later switches to
   `bge-small` for the RAM/CPU savings discussed separately, does this service need to support
   BOTH `bge-base` and `bge-small` simultaneously (multi-model serving), or just standardize on
   one? (Multi-model support is a real feature of TEI and most serving frameworks, so this is not
   a blocker either way — just needs a decision.)
2. Decide the batching window size and max batch size empirically once real traffic patterns are
   known (start with TEI's sane defaults, tune from there).
3. Decide where this service actually runs — same VPS as `andras-job-search`, or its own
   lightweight host — and how `andras-job-search`'s `scorer`/`scorer-service` point at it
   (an env var like `EMBEDDER_URL`, mirroring the existing `SCORER_URL` pattern already used for
   `andras-job-search`'s own scorer-service).
4. Migration plan for `andras-job-search`: replace `scorer/score.py`'s `TextEmbedding(...)`
   instantiation and `scorer/train.py`'s equivalent with an HTTP client call to this service;
   keep a fallback/local-load path only if the shared service must be optional.
5. Only after (1)-(4) are settled and same-project sharing is proven stable: revisit whether to
   expose this to the other tenant's stack, with their explicit agreement.

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
| **bge-small (384d)** | lower | good, CPU-fast | andras retrains down | **yes** |
| **bge-base (768d)** | ~2× | better | job-search retrains up | **likely no** |

**Recommendation:** given the <2GB target (§12), standardise on **bge-small (384d)** unless a fit-quality
A/B proves bge-base materially better. Cross-instance decision (§6). *(Update 2026-07-25: andras has been
retrained onto the shared model; parity results pending.)*

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
if**: (1) shared model is **bge-small** (bge-base ~2× likely breaks it); (2) **embedder-only image**;
(3) **bounded `max_batch_tokens`** so training streams as micro-batches. **Measure peak RSS on the box —
do not promise it.** Today's ~1.9GB is the whole scorer-service container; an embedder-only TEI with
bge-small + micro-batching should land ~1–1.5GB, but onnxruntime's arena is the real floor.

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

Skeleton built (TEI + thin FastAPI gateway with two priority lanes, network-only, bge-small pinned).
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

**Still open:** never run on the box — parity (§14.1), peak RSS (§14.5), and lane latency (§14.8) are
all unmeasured; andras-job-search is still on in-process `fastembed` with no `embedder-net` wiring.
Key decisions unchanged: standardise on bge-small pending a quality A/B (§10, andras already
retrained); default to VPS training via the shared embedder (§11); hold <2GB with bge-small +
embedder-only + micro-batching, verified on the box (§12); enforce version pinning (§13); pass the
test plan (§14) before any switch.
