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

The gateway publishes `127.0.0.1:8080` — loopback only, so it is reachable from the host running
the tests but not from the network. Consumer containers do not use that port; they reach the
gateway by service name over `embedder-net`.

That mapping is optional and only exists for host-side tooling. **These tests and
`scripts/load_rss.py` need it**; `scripts/verify_stack.py` does not (it falls back to `docker exec`
on the gateway container). If you comment the `ports:` line out, re-add it before running the gates
or every integration test fails with one "gateway not reachable" line.

If `EMBEDDER_URL` is set but nothing is listening, the integration tests fail immediately with one
line saying so (see `conftest.py`) rather than nine httpx tracebacks. They **fail** rather than
skip on purpose — a silent skip is how §14.1 parity went unrun while the suite looked green.

## Tunables the integration tests read

| Env | Default | Used by |
|---|---|---|
| `EMBEDDER_URL` | *(unset → skip)* | all integration tests |
| `EMBEDDER_TOKEN` | *(none)* | all, when auth is on |
| `EMBEDDER_MODEL` | `BAAI/bge-base-en-v1.5` | `test_parity.py` (which model to load locally) |
| `SCORE_LATENCY_BUDGET_S` | `10.0` | `test_priority_lane.py` — set from real box numbers |
| `FLOOD_TEXTS` / `FLOOD_WORKERS` | `128` / `2` | `test_priority_lane.py` — sustained load, not a full corpus |
| `FLOOD_TIMEOUT_S` | `600` | `test_priority_lane.py` — a flood request that outlives the window is fine, not a failure |
