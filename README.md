# job-search-app

Umbrella repo for the job-search SaaS build. **First component: the shared embedder service** — one
resident copy of a BGE model serves every tenant's scoring **and** training over HTTP, instead of each
stack loading its own ~1.9GB copy. See [`docs/PLAN.md`](docs/PLAN.md) for the full design (a reference
copy also lives in the job-search repo at `docs/EMBEDDER_SHARING_PLAN.md`; both now carry the
§4c/§9–§15 sections).

**Status:** skeleton, code-complete. Serves `/health` + `/embed` + `/embed/bulk`; priority lanes,
bounded queue, and a **backend-verified** version contract are wired, and the gateway's own logic is
covered by tests that need no docker. Not yet parity-verified, load-tuned, or RSS-measured on the
VPS — those gates run there (see [Tests](#tests)).

## Architecture

```
  consumer stacks (job-search, andras, …)            embedder-net (shared docker network)
     scorer-service / trainer  ──HTTP──▶  embedder-gateway (FastAPI)  ──▶  tei (HF TEI, CPU)
                                          • /embed      fast lane            loads BGE ONCE
                                          • /embed/bulk bulk lane            token batching +
                                          • /health     model/dim/version    length bucketing
```

- **tei** — HuggingFace Text Embeddings Inference. Loads the model once, does the dynamic
  (token-based) batching + length bucketing. `<2GB` target lives here (`mem_limit: 2g`).
- **embedder-gateway** — thin FastAPI. Two priority lanes (fast `/embed` for on-arrival scoring vs
  bulk `/embed/bulk` for training), a bounded dispatch queue (503 backpressure instead of unbounded
  growth), our `/health` version contract, optional bearer auth. Owns no model.

### The version contract (PLAN §13)

`/health` reports `{ok, model, dim, normalize, version, verified, verify_state, verify_detail,
tei_ok, queue_depth}`. `ok` is true **only after the gateway has probed the live TEI backend** and
confirmed both that `/info.model_id` matches `EMBEDDER_MODEL` and that a real embed returns exactly
`EMBEDDER_DIM` floats. Reporting the `.env`'s claims unchecked would let a config drift satisfy
every caller's `assert_compatible` while the vectors came from a different model — silently wrong
scores, no error. On a mismatch the gateway stays unhealthy **permanently** and both embed
endpoints return 503, so callers fail closed (§14.6) instead of scoring against the wrong vector
space.

## Run

```bash
cp .env.example .env            # defaults to BAAI/bge-base-en-v1.5 (768d)
docker compose up -d --build
curl -s http://127.0.0.1:8080/health   # loopback-only port, published for host-side testing
```

## VPS bring-up order (PLAN §14 gates)

Do these in order; each one is a stop-the-line gate, because switching a consumer over before they
pass is how you get *silently wrong scores* rather than a visible outage.

1. `docker compose up -d --build`, then `curl .../health` until `"verified": true`. First boot
   downloads the model — `verify_state` reads `unreachable` until TEI is up, `mismatch` if `.env`
   disagrees with what TEI loaded (fix the config, don't work around it).
2. `pytest tests/ -v` with `EMBEDDER_URL` set — health shape, version assert, batch invariance.
3. `pytest tests/test_parity.py -v` with `fastembed` installed — **the decision point.** Vectors
   match → existing classifiers keep working. They don't → retrain on TEI vectors (which §10's model
   standardisation forces anyway). Do not skip this and hope.
4. `python3 scripts/load_rss.py` — peak RSS under 2GB with 3 callers + a cold-start bulk embed. Over
   budget → lower `TEI_MAX_BATCH_TOKENS` / `EMBEDDER_BULK_CHUNK` and re-measure.
5. `pytest tests/test_priority_lane.py -v` — set `SCORE_LATENCY_BUDGET_S` from what step 4 showed.
6. Only then set `EMBEDDER_URL` on **one** consumer (job-search) and watch it. Unsetting it reverts
   to in-process `fastembed` with no code change — that is the rollback.

Leave the loopback `ports:` mapping in place throughout. It is not a public port and you need it
for steps 1-5; removing it only breaks the gates. Real exposure requires `EMBEDDER_AUTH_TOKEN` and
TLS, which is a separate decision.

## Consumer wiring

Attach a consumer stack's compose to the shared network and call the gateway by service name:

```yaml
# in job-search / andras docker-compose.yml
networks:
  embedder-net:
    external: true
services:
  scorer-service:
    networks: [default, embedder-net]
    environment:
      EMBEDDER_URL: http://embedder-gateway:8080
```

Then in the consumer's scorer, replace the in-process `TextEmbedding(...)` with the
[`examples/client.py`](examples/client.py) `EmbedderClient` — `embed_score(...)` on the serving path
([`scorer/score.py:80`](../job-search/scorer/score.py) equivalent) and `embed_bulk(...)` in the
trainer's `_embed`. **Call `assert_compatible(model, dim)` at startup** so a vector-space mismatch
refuses to score instead of scoring wrong (PLAN §13).

## ⚠ Two things to settle before trusting it (PLAN)

1. **Model standardisation (§10) — decided: `bge-base` (768d).** RAM was the only argument for
   `bge-small` and measurement disproved it (282 MB peak, §12), so the choice comes down to fit
   quality. **Both tenants retrain**: job-search was on 384d, andras had already been moved to 384d,
   and there is no conversion between vector spaces. Budget ~3× the bge-small embed time.
2. **Parity vs retrain (§14.1 / `tests/test_parity.py`).** TEI's pooling/normalisation may not match
   the old in-process `fastembed` bit-for-bit. Either configure TEI to match, or (cleaner) adopt TEI
   as the single source of truth and **retrain all classifiers on TEI vectors** — which the §10
   standardisation already forces anyway.

## Tests

Full gate-by-gate map in [`tests/README.md`](tests/README.md). Two tiers:

**No stack needed** — run anywhere, including CI:

```bash
pip install -r tests/requirements.txt
pytest tests/ -v          # integration tests skip while EMBEDDER_URL is unset
```

- `test_gateway_contract` — the gateway against a stubbed TEI: model/dim mismatch refuses to serve,
  unreachable backend 503s, bulk chunks reassemble in input order, size caps, auth.
- `test_fail_closed` — a caller pointed at a dead embedder raises rather than returning a vector
  it could score (§14.6).

**On the VPS, against a running stack:**

```bash
docker compose up -d --build
EMBEDDER_URL=http://localhost:8080 pytest tests/ -v
python3 scripts/load_rss.py --url http://localhost:8080     # §14.5, exits 1 over the 2GB budget
```

- `test_health` / `test_version_assert` — the `/health` shape, and that `ok` is backend-verified and
  a wrong (model, dim) is refused (§14.2/§13).
- `test_batch_invariance` — same text alone == inside a big mixed batch (accuracy gate, §14.7).
- `test_priority_lane` — a score request still returns within budget under a training flood (§14.8).
- `test_parity` — TEI vs local fastembed cosine (needs `fastembed` + same model; decides
  match-or-retrain).

Not in this repo: score equivalence (§14.3) and cache incrementality (§14.4) — both need a trained
classifier and its embed cache, which live consumer-side.
