# Shared Embedder Service — Design Plan (SUPERSEDED)

> ⚠ **Historical.** This is the original planning document, kept for the reasoning that led here.
> It was written before any code existed and does not reflect what was built. The maintained plan —
> with the measured numbers, the decisions that reversed (bge-small → bge-base), and the design
> corrections the VPS run forced (worker pools, batch ceilings, backend-verified health) — is
> [`embedder/docs/PLAN.md`](../../embedder/docs/PLAN.md). The service itself lives in
> [`embedder/`](../../embedder/) and is deployed.

**Origin:** came out of a live incident on the shared VPS (see `andras-job-search/docs/CHANGELOG.md`,
2026-07-24) — running multiple job-search instances side by side (this instance + a friend's
`andras-job-search`) meant each one loaded its own private ~1.4-1.9GB copy of the same universal
embedding model, contributing to a memory crunch that killed a scoring request mid-flight under
load.

## 1. Problem

Every job-search deployment that scores postings locally (`SCORING_ENGINE=model`) needs to turn
job text into a vector before its trained classifier can use it. That text-to-vector step
(the "embedder") is:

- **Universal** — a pre-trained, general-purpose model (e.g. `BAAI/bge-base-en-v1.5`). It is not
  trained or customized by any individual job-search deployment; it is a fixed, frozen artifact
  downloaded from HuggingFace. Same input text -> same output vector, always, regardless of whose
  instance is asking or what their trained classifier looks like.
- **Expensive to hold in memory** — ~1.4-1.9GB resident RAM once loaded (observed), because it is
  a real transformer network, not a lookup table.
- **Expensive to run** — every `/score` call is genuine neural-net inference (a forward pass per
  job posting's text), scaling with batch size and text length.
- **Duplicated today** — every instance that scores locally loads its own private copy: once in
  its always-on serving container, and again (briefly) in its one-shot training container during
  a retrain. On a shared VPS running multiple people's job-search stacks side by side (the exact
  Option B model in `FRIENDS_ROLLOUT.md`), this means N people each pay the full RAM cost
  independently for byte-identical model weights.

## 2. Goal

A single, shared embedding service that any job-search instance (this one, a friend's under
Option B, or later a SaaS tenant — see `SAAS_PLATFORM_PLAN.md`) can call over HTTP instead of
loading its own copy of the embedder. Each instance keeps its own private, personalized
classifier (the actual "job fit" model) — only the universal text-to-vector step is centralized.

**Explicitly NOT a goal:** centralizing the classifier, the training data, or anything
personalized. Those stay private per instance. Only the shared, non-personal, deterministic
embedding step is centralized.

## 3. Key design question: booking/lock vs concurrent vs batched

### Option A — Exclusive booking queue (one client at a time, others wait) — REJECTED
Embedding inference is a **pure, stateless function** — it reads input text and produces a
vector; it does not mutate the model or any shared state. There is no correctness reason to
serialize callers. Locking would only add wall-clock latency: if client A's batch takes 2 minutes
and client B's also takes 2 minutes, serializing makes it 4 minutes total instead of letting both
share the CPU concurrently in roughly the same total compute time.

### Option B — Naive full concurrency — works, but inefficient under load
ONNX Runtime (what `fastembed` uses under the hood) is thread-safe for concurrent inference
calls, but many small simultaneous requests are less CPU-efficient than fewer, larger batched
calls — uncoordinated concurrent load can degrade throughput for everyone.

### Option C — Dynamic (continuous) batching — CHOSEN
The production-standard pattern (NVIDIA Triton, HuggingFace's own embedding server, BentoML,
TorchServe): a short internal queue that accumulates whichever requests arrive within a small
time window (tens of milliseconds — NOT minutes) or until a batch-size cap is hit, then processes
them as one batched `embed()` call. No thrashing, near-zero added latency, better throughput than
either A or naive B. This is the technically correct version of "booking": coordinate access, but
at the millisecond/batch-size scale, not the whole-request scale.

## 4. Build vs adopt

- **4a. Custom lightweight service** — FastAPI wrapping `fastembed.TextEmbedding` + a hand-rolled
  batching queue. Full control, more code to maintain.
- **4b. HuggingFace Text Embeddings Inference (TEI)** — purpose-built Rust server, continuous
  batching, health checks, metrics, direct BAAI/bge support already built in.
- **Recommendation: prototype with TEI first.** Standing it up is a docker-compose service, not
  new code, and it directly solves the batching problem without maintaining that logic ourselves.

## 4b. Batching optimization — the concrete knobs (Triton/TEI dynamic batching)

Option C (§3) is the right pattern; this section pins the actual production knobs so it is buildable,
because a central embedder serving **both real-time scoring and bulk training for multiple users** must
be fast *and* correct at the same time.

### The core knobs (Triton names / TEI equivalents)

| Purpose | Triton (`dynamic_batching`) | TEI flag | Effect |
|---|---|---|---|
| Target batch to dispatch | `preferred_batch_size: [..]` | `--max-batch-tokens` (token-based) | Dispatch as soon as this size/token-count is hit. |
| Latency⇄throughput dial | `max_queue_delay_microseconds` | internal queue window | Wait up to this long to fill a batch; **no request waits longer**. Small = low latency, large = fatter batches. |
| Bound the queue | `max_queue_size` | `--max-concurrent-requests` / `--max-batch-requests` | Backpressure instead of unbounded memory growth. |
| Priority lanes | `priority_levels` + `default_priority_level` | (front a small priority queue) | Higher-priority requests **bypass** lower-priority ones. |

Triton dispatches when **either** a preferred size is reached **or** `max_queue_delay` expires, whichever
comes first — that is the whole dynamic-batching contract.

### The decisive nuance: scoring and training want OPPOSITE settings

The two workloads this embedder must serve have conflicting goals, so a single queue tuned for one
starves the other:

| Workload | Goal | Batch preference | Queue delay |
|---|---|---|---|
| **On-arrival scoring** (a job lands, a user waits) | **low latency** | small batches, dispatch fast | **tiny** (~5–10 ms) |
| **Training** (bulk, offline, cache-fed) | **throughput** | large batches, saturate CPU | **larger** (~50–100 ms or fill to `max_batch_tokens`) |

**Solution — two priority lanes on one instance:**
- **High-priority "score" lane** — tiny `max_queue_delay` (a few ms), small preferred batch → an
  on-arrival job is embedded almost immediately and **bypasses** an in-progress training flood.
- **Bulk "train" lane** — large `max_batch_tokens`, higher delay tolerance → fills fat, CPU-efficient
  batches, and **yields** to the score lane.

Callers tag which lane they want (a priority field, or two endpoints/queues). This is exactly Triton's
`priority_levels`; with TEI it's approximated by fronting a small priority queue (or, if simpler, two TEI
containers — but that doubles RAM and fights the <2GB budget, so prefer one instance with a priority
queue). This is what makes it *speedy for scoring* without *starving training throughput*.

### Padding / length bucketing (free speed, no accuracy cost)

A batch pads every sequence to the batch's longest, so mixing a 20-token title with a 2000-token JD
wastes compute on padding. **Sort/bucket by sequence length within a batch** (TEI's token-based batcher
does this by design) so each batch is length-homogeneous — a real throughput win that **does not change
any vector**.

### CPU + <2GB constraints (what NOT to copy from NVIDIA)

The VPS is CPU-only with a hard <2GB budget (§12). Two NVIDIA/Triton defaults must be **avoided**:
- **Concurrent model execution / `instance_group` > 1** (multiple resident model copies) — a RAM
  multiplier. Keep **one** model instance; get parallelism from dynamic batching + onnxruntime threads,
  not from replicas.
- **Unbounded `max_batch_tokens`** — a giant batch spikes the onnxruntime working set past 2GB. Cap it
  (the micro-batch lever from §12); the token cap *is* the memory cap.

Start points to tune empirically (§8.2): score lane `max_queue_delay ≈ 5 ms`, small preferred batch;
train lane `max_batch_tokens` sized so peak RSS stays < 2GB on the box (measure — do not guess).

### Accuracy invariant (batching must be output-equivalent)

Embeddings are **per-sequence independent** — there is no cross-sequence attention — so a correctly
masked batch produces **the exact same vector** for a text whether it is alone or grouped with others.
Batching is a speed optimization, **never** an accuracy trade, *provided* padding is masked correctly.
Guard it in the test plan:
- §14.1 parity (remote batched == in-process single) **plus** a **batch-invariance test**: the same text
  embedded alone vs inside a large mixed-length batch must return an identical vector (within float
  tolerance). If that ever fails, padding is leaking into the pooled vector — a correctness bug, not a
  tuning issue.

## 5. Architecture

```
                     ┌─────────────────────────────┐
                     │   shared embedder service    │
                     │  (TEI or custom FastAPI)      │
                     │  loads BAAI/bge-* ONCE        │
                     │  POST /embed  {texts: [...]}  │
                     │       -> {vectors: [[...]]}   │
                     └───────────────┬───────────────┘
                                     │  shared Docker network (external: true)
              ┌──────────────────────┼──────────────────────┐
              │                      │                      │
    ┌─────────▼─────────┐  ┌─────────▼─────────┐  ┌─────────▼─────────┐
    │ job-search (Ruben) │  │ andras-job-search  │  │  (future tenant)  │
    │  scorer-service     │  │  scorer-service    │  │  scorer-service   │
    │  (own classifier)   │  │  (own classifier)  │  │  (own classifier) │
    └────────────────────┘  └───────────────────┘  └───────────────────┘
```

## 6. Multi-tenancy: same-project vs cross-project

- **Same-project (this instance's own `scorer` + `scorer-service`)**: fully actionable
  unilaterally, no coordination needed. First rollout target.
- **Cross-instance (sharing with a friend's or future tenant's independently-run deployment)**:
  requires their code to change too, and introduces a real coupling — if the shared embedder has
  a problem, everyone attached loses scoring simultaneously. Only pursue with explicit, informed
  agreement from whoever owns the other instance.

**Recommended rollout order:** same-instance sharing first, prove it out, THEN decide separately
whether to extend it to other tenants (including, eventually, real SaaS customers).

## 7. API shape (sketch)

```
POST /embed  { "texts": ["title\ndescription...", "..."] }
->  { "vectors": [[...]], "model": "BAAI/bge-base-en-v1.5", "dim": 768 }

GET /health  -> { "ok": true, "model": "BAAI/bge-base-en-v1.5", "dim": 768 }
```

Callers keep their own `build_text` conventions (title + description, truncation) — the embedder
only turns whatever text string it's given into a vector.

## 8. Open questions before implementation

1. Single embedding model, or multi-model serving (if an instance later switches to `bge-small`)?
2. Batching window/size — tune empirically once real traffic patterns are known.
3. Where this runs, and how instances point at it (`EMBEDDER_URL` env var, mirroring the existing
   `SCORER_URL` pattern).
4. Migration plan per instance: replace the direct `TextEmbedding(...)` instantiation in
   `scorer/score.py` and `scorer/train.py` with an HTTP client call.
5. Only after 1-4 are settled: revisit cross-tenant sharing, with explicit agreement.

## 9. Current wiring (verified against the code, 2026-07-24)

How the embedder is actually used today, so the extraction is surgical and nothing is overlooked:

- **Primary scoring is the local model, NOT Haiku.** `config.yaml` defaults `SCORING_ENGINE=model`;
  Haiku only shadow-grades / verifies / labels. So the embedder is load-bearing — without it, the free
  Phase-2 path is gone and scoring falls back to paying Haiku on every job.
- **Serving:** the embedder loads **in-process inside the `scorer-service` container** —
  `scorer/score.py:56` `TextEmbedding(model_name=self.embed_model)`, used at `score.py:80`
  `self._embedder.embed(texts)`. Resident ~1.9GB, `mem_limit: 3g` (docker-compose.yml). It sits in the
  **same process** as the sklearn head; flow is `text → embed → hstack(features.py) → model.predict`.
- **Pipeline + watcher do NOT load the embedder** — they already POST job text to
  `http://scorer-service:8000/score` (`service.py`). The service boundary exists at the *scorer*; this
  plan pushes it one layer down to the *embedder*.
- **Training:** the `scorer` trainer (one-shot, `tools` profile) loads its **own** embedder copy —
  `train.py:413` `TextEmbedding(...)`, embedding via `train.py:141` `_embed`. This is the **double-copy**
  the compose header warns about (a bare `up` once launched the trainer alongside serving → two resident
  models → box stacked).
- **Embed cache:** `train.py:141-173` keeps a **persistent on-disk cache** keyed by `md5(text)` per
  model. A retrain embeds **only new rows since the last one** — the corpus bulk is cached (“a ~25-minute
  CPU embed into seconds after the first pass”). Cold start (first train / wiped cache / new model) is the
  only full-corpus embed.

**Extraction seam:** replace `score.py:80` and the `train.py::_embed` inner loop with an HTTP call to the
shared `/embed`; keep `features.py` (cheap regexes, hstack locally) and `model.predict` **local**. Only
the universal `text → BGE vector` step goes remote.

## 10. ⚠ Model mismatch between the two live instances — resolve before sharing

**The overlooked blocker.** The two instances that would share the embedder are **not on the same BGE
model today** (verified 2026-07-24):

| Instance | Configured model | Dim | Source |
|---|---|---|---|
| **this repo (job-search)** | `BAAI/bge-small-en-v1.5` | **384** | no `.env` override → `train.py:46` default |
| **andras-job-search** | `BAAI/bge-base-en-v1.5` | **768** | explicit `SCORER_EMBED_MODEL` in andras `.env` |

**Why this matters:** 384-dim and 768-dim are **different vector spaces**. A single shared embedder
serves **one** model → **one** dimensionality. It physically cannot feed both classifiers as they stand.
A classifier trained on bge-small (384-wide input) will error or produce garbage on a bge-base (768-wide)
vector, and vice versa. (This resolves open question §8.1 and corrects the §7 sketch, which shows
bge-base-768 as if it were settled — it is not, for this instance.)

**Resolution — pick ONE shared model; the off-model instance retrains its classifier on it.** Retraining
is mandatory, not optional: the sklearn head’s input width is fixed by the embedder’s dim. The cache does
not help across a model switch (different model = full cold re-embed of that instance’s corpus once).

**Model choice tradeoff:**

| Shared model | RAM | Quality | Who retrains | Fits <2GB? |
|---|---|---|---|---|
| **bge-small (384d)** | lower | good, CPU-fast | **andras retrains down** | **yes** (this repo already on it) |
| **bge-base (768d)** | ~2× | better semantics | **this repo retrains up** | **likely no** (breaks the <2GB target) |

**Recommendation:** given the explicit <2GB goal (§12), standardize on **bge-small (384d)** and have
andras retrain onto it — **unless** a fit-quality A/B (bge-small vs bge-base on the same labels) shows
bge-base is materially better for the scoring task, in which case the memory budget is revisited. This is
a **cross-instance decision** (§6) — it needs the andras owner’s explicit agreement, because it forces a
retrain on their side.

## 11. Training placement — VPS-via-shared-embedder (default) vs local-and-upload (fallback)

The shared embedder **removes the reason to avoid training on the VPS**: today VPS training is discouraged
because the trainer loads a *second* embedder copy (§9). With a remote embedder, the trainer just
`POST /embed` and loads **no embedder at all** — its RAM becomes just sklearn (small).

| Option | Status | Notes |
|---|---|---|
| **B) Train on VPS via the shared embedder** | **Recommended default** | Double-copy problem gone; `scorer` trainer calls shared `/embed`; caller-side cache keeps retrains incremental. |
| **A) Train locally, upload `scorer.joblib`** | **Keep as fallback** | The artifact is portable (records its `embed_model`); good for a heavy cold-start you’d rather run on a dev box. |

- **Embed cache stays caller-side** (each instance’s labels differ; the embedder is stateless). Do not
  move it into the shared service in phase 1.
- **Optional later win:** a *shared* embed cache on the service (same text → same vector for everyone →
  one instance skips embedding a job another already did; EU-remote job sets overlap heavily). Deterministic
  and safe, but build it only after the base sharing is proven.
- **Constraint (both placements):** training and serving must embed with the **exact same model +
  version** the classifier was trained against (§13), or the vectors are the wrong space/width.

## 12. Load & memory analysis + the <2GB conditions

Traffic profile: 2–3 instances, scoring within a ~25-min window, usually not simultaneous, occasionally
overlapping on training.

| Load | Reality |
|---|---|
| **Serving** | Trivial. ~3 instances × ~15 jobs / 15 min ≈ **3 jobs/min**; sub-second on bge-small. |
| **Training (incremental)** | Light. Cache → only new rows embed; tens of docs per retrain → seconds. |
| **Training (cold-start)** | The only spike: full corpus × ~0.5 s/doc (e.g. 2000 rows ≈ 17 min CPU). Two overlapping cold trains = the sole contention case — **CPU-time, not a memory blowup** (batches are bounded). |
| **Concurrency** | Fine. onnxruntime is thread-safe; the dynamic-batching queue (§3 Option C) merges 2–3 overlapping callers. |

**Staying under 2GB — the memory driver is batch size, not caller count.** onnxruntime’s working set
scales with `batch_size`; a giant single batch spikes RAM, **micro-batches cap it** (and naturally
serialize CPU across callers). So micro-batching is the right lever. <2GB is **achievable only if all
three hold**:

1. **Shared model is bge-small (384d).** bge-base (768d) is ~2× and **will likely break <2GB** (§10).
2. **Embedder-only image** — no sklearn / jobspy / scorer baggage resident beside it.
3. **Bounded `batch_size` (32–64)** so training’s large embed streams as micro-batches, not one big tensor.

**Do not promise <2GB — measure it.** Today’s ~1.9GB is the *whole* `scorer-service` container (embedder
+ sklearn + FastAPI + numpy). A dedicated embedder-only service with bge-small + micro-batching should
land ~1–1.5GB, but onnxruntime’s memory arena is the real floor — **verify peak RSS on the box** before
trusting the target.

## 13. Version-pinning correctness constraint (must-do)

A classifier is bound to the exact vectors it was trained on. If the shared embedder drifts, every
attached classifier silently degrades with **no error**. Guardrails:

- `/health` exposes `{model, embedder_version, dim}`. Each caller **asserts** its artifact’s
  `embed_model` + `dim` matches **before trusting any score**; on mismatch it **refuses to score (loud)**,
  never scores on the wrong space.
- `build_text` + `_MAX_TEXT=4000` stay **identical caller-side** — send the already-built text; the
  embedder only vectorizes the string it’s handed (§7).
- **Pin the `fastembed` / model version** on the service (pooling + normalization determinism). A version
  bump that changes vectors invalidates every attached classifier → treat it as a **coordinated
  migration** (everyone retrains), exactly like a SACRED rubric change.

## 14. Pre-deploy test plan (prove it in reality first)

Nothing switches until these pass:

1. **Vector parity** — for a fixed model+version, assert the remote `/embed` vector **equals** the old
   in-process `TextEmbedding` vector (within float tolerance) on a sample of real job texts. This is the
   gate: if vectors match, the classifier is unaffected.
2. **Version/dim assertion** — caller correctly **refuses** to score when `/health` model/dim ≠ its
   artifact (inject a mismatch).
3. **Score equivalence end-to-end** — scores from the remote-embedder path **==** the in-process path on a
   frozen job set with the same classifier.
4. **Cache incrementality** — retrain twice; the second run embeds ~0 new (cache hit), confirming
   incremental training holds through the HTTP boundary.
5. **Load + memory** — 3 concurrent callers plus one cold-train burst; measure **peak RSS < 2GB** and
   acceptable latency (validates §12’s conditions on the real box).
6. **Failure blast-radius** — embedder down → callers **fail closed** to “cannot score” (surfaced), not
   garbage scores; confirms the §6 coupling risk is handled.
7. **Batch-invariance** (accuracy gate, §4b) — the same text embedded **alone** vs inside a large
   mixed-length batch returns an **identical** vector (within float tolerance). A failure = padding
   leaking into the pooled vector, i.e. a correctness bug in the batcher, not a tuning knob.
8. **Priority-lane latency** — under a sustained training flood on the bulk lane, a single on-arrival
   score request still returns within the target (e.g. < ~50 ms embed time); proves the score lane
   actually bypasses the bulk lane (§4b) rather than queueing behind it.

## 15. Status

Planning only. No code written yet. Key decisions captured above: extract only the universal
`text → vector` step (§9); **standardize the shared BGE model — currently mismatched, this repo on
bge-small-384, andras on bge-base-768 — recommend bge-small pending a quality A/B, andras retrains (§10)**;
serve it with dynamic batching on **one** instance split into two priority lanes — a low-latency score
lane and a bulk train lane (§4b); default to training on the VPS via the shared embedder with
local-upload as a fallback (§11); hold the
<2GB target with bge-small + embedder-only image + micro-batching, verified on the box (§12); enforce
version pinning (§13); and pass the parity/equivalence/load test plan (§14) before any switch.
