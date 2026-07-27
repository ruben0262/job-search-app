# tests — cross-service behaviour tests

Tests that span more than one service live here. A test that exercises a single service lives with
that service (`services/embedder/tests/` is the existing example and the model to copy).

Full specification, including the anti-flake rules and the write-order: `docs/saas/TEST_STRATEGY.md`.

## The ones that belong here

| Test | What it protects | Written when |
|---|---|---|
| **T1 tenant isolation** | No user can read or write another user's rows. **Release blocker.** Isolation is enforced at the data layer rather than the container layer (`PLATFORM_ARCHITECTURE.md` §2) — that is a higher correctness bar, accepted deliberately, and this test is what makes it safe | As soon as the first per-user table exists (Phase 1) |
| **T5** | Scheduler fairness / restart safety | With the scheduler, same commit |
| **T10** | End-to-end onboarding | Phase 3 |

`T2` (the dedup regression suite) is written **against today's SQLite behaviour before the
`store.py` port begins** and lives with `libs/corpus`. It is the critical path's safety net; the
port is not started without it.

## Running

```bash
pip install -r requirements-dev.txt
pytest tests/                    # cross-service
pytest services/embedder/tests/  # the embedder's own suite
```

Integration tests that need a live stack read their target from an environment variable and
**fail** rather than skip when it is set but unreachable — a silent skip is how a parity gate went
unrun for days while looking green (`services/embedder/tests/conftest.py`).
