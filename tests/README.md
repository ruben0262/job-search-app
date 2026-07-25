# Test plan coverage (PLAN §14)

Nothing switches to the shared embedder until these pass. Two of the eight gates live in the
**consumer** repo, not here — they need a trained classifier and its embed cache, which this
service deliberately does not have (it is stateless, PLAN §11).

| § | Gate | Where | Needs a stack? |
|---|---|---|---|
| 14.1 | Vector parity vs in-process `fastembed` | `test_parity.py` | yes + `fastembed` |
| 14.2 | Version/dim assertion — caller refuses on mismatch | `test_version_assert.py` | yes |
| 14.3 | Score equivalence end-to-end (remote == in-process) | **consumer repo** (`job-search/scorer`) | yes |
| 14.4 | Cache incrementality — 2nd retrain embeds ~0 | **consumer repo** (cache is caller-side) | yes |
| 14.5 | Load + peak RSS < 2GB | `scripts/load_rss.py` (run on the VPS) | yes + docker |
| 14.6 | Failure blast radius — callers fail closed | `test_fail_closed.py` | **no** |
| 14.7 | Batch invariance (accuracy) | `test_batch_invariance.py` | yes |
| 14.8 | Priority-lane latency under a training flood | `test_priority_lane.py` | yes |
| — | `/health` contract shape | `test_health.py` | yes |

## Running

```bash
pip install -r tests/requirements.txt

pytest tests/ -v                                   # only the no-stack gates (§14.6); rest skip

docker compose up -d --build                       # on the VPS
EMBEDDER_URL=http://localhost:8080 pytest tests/ -v # everything except §14.1's fastembed extra

pip install fastembed numpy                        # §14.1 only — heavy, downloads the model
EMBEDDER_URL=http://localhost:8080 pytest tests/test_parity.py -v

python3 scripts/load_rss.py --url http://localhost:8080   # §14.5, exits 1 over budget
```

The gateway's port is not published by default (network-only, PLAN §6). For local testing either
uncomment `ports:` on `embedder-gateway` in `docker-compose.yml`, or run the tests from a container
attached to `embedder-net` with `EMBEDDER_URL=http://embedder-gateway:8080`.

## Tunables the integration tests read

| Env | Default | Used by |
|---|---|---|
| `EMBEDDER_URL` | *(unset → skip)* | all integration tests |
| `EMBEDDER_TOKEN` | *(none)* | all, when auth is on |
| `EMBEDDER_MODEL` | `BAAI/bge-base-en-v1.5` | `test_parity.py` (which model to load locally) |
| `SCORE_LATENCY_BUDGET_S` | `5.0` | `test_priority_lane.py` — set from real box numbers |
| `FLOOD_TEXTS` / `FLOOD_WORKERS` | `512` / `2` | `test_priority_lane.py` |
