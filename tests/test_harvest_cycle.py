"""One harvest cycle, end to end, with fake sources.

What is worth testing here is the **orchestration**: the order of stages, that the budget is
respected, that a dead source cannot sink the run, that overlapping runs cannot both proceed, and
that dormant users cost nothing. Whether Greenhouse's JSON parsed is not testable without a network
and is covered structurally instead (`tests/test_harvester_sources.py`).

No network, per `TEST_STRATEGY.md` §2.2 — sources are injected.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("sqlalchemy")
import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from libs.contracts.embedder import EmbedderClient, EmbedderVersion
from libs.db.models import CorpusJob, User
from libs.tenant.repository import TenantRepository
from services.harvester.cycle import harvest_lock, run_cycle
from services.harvester.sources import JobLink

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set (no live Postgres)")

DIM = 8
SERVED = {"ok": True, "model": "m", "dim": DIM, "version": "m|dim=8|norm=1"}
EXPECTED = EmbedderVersion("m", DIM, "m|dim=8|norm=1")


def _embedder() -> EmbedderClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json=SERVED)
        import json
        texts = json.loads(request.content)["texts"]
        return httpx.Response(200, json={"vectors": [[0.5] * DIM for _ in texts]})

    return EmbedderClient(client=httpx.Client(
        base_url="http://e", transport=httpx.MockTransport(handler)))


@pytest.fixture()
def ctx():
    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    s = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield s
    finally:
        s.close()
        trans.rollback()
        conn.close()


def _active_user(s, keywords=("python",)) -> User:
    u = User(email=f"{uuid.uuid4()}@example.test", password_hash="x", timezone="UTC",
             status="active", active=True)
    s.add(u)
    s.flush()
    TenantRepository(s).save_config(u.id, {"keywords": list(keywords), "locations": ["london"]})
    s.flush()
    return u


def _link(title="Backend Engineer", company=None, snippet="Go, gRPC. " * 30, **kw) -> JobLink:
    return JobLink(
        url=f"https://boards.greenhouse.io/acme/jobs/{uuid.uuid4().hex}",
        title=title, company=company or f"Acme{uuid.uuid4().hex[:8]}",
        location="London", source="greenhouse", snippet=snippet, **kw)


def _sources(**by_key):
    """Enable named sources; every other source stays disabled."""
    return {key: {"enabled": True, **cfg} for key, cfg in by_key.items()}


# ── the happy path ────────────────────────────────────────────────────────────────────────────

def test_a_cycle_ingests_dedups_and_embeds(ctx):
    """The whole loop: demand → sources → dedup → upsert → embed once."""
    s = ctx
    _active_user(s)
    found = [_link(), _link()]

    with _embedder() as embedder:
        report, _ = run_cycle(
            s, embedder=embedder, expected_embedder=EXPECTED,
            source_configs=_sources(remotive={}),
            run_source=lambda source, cfg, repo: list(found))
    s.flush()

    assert report.links_found == 2
    assert report.new_jobs == 2
    assert report.embedded == 2
    stored = list(s.scalars(CorpusJob.__table__.select().with_only_columns(CorpusJob.id)))
    assert len(stored) >= 2


def test_the_same_job_twice_is_ingested_once(ctx):
    """Two sources listing one posting is the normal case, not the exception."""
    s = ctx
    _active_user(s)
    link = _link()

    with _embedder() as embedder:
        first, _ = run_cycle(s, embedder=embedder, expected_embedder=EXPECTED,
                             source_configs=_sources(remotive={}),
                             run_source=lambda *a: [link])
        s.flush()
        second, _ = run_cycle(s, embedder=embedder, expected_embedder=EXPECTED,
                              source_configs=_sources(remotive={}),
                              run_source=lambda *a: [link])
    s.flush()

    assert first.new_jobs == 1
    assert second.new_jobs == 0
    assert second.duplicates.get("url") == 1


def test_a_snippet_saves_the_description_fetch(ctx):
    """Sources that supply usable text save a whole fetch per job — the difference between a
    15-minute cycle and an hour, and on the proxied tier a bandwidth saving too."""
    s = ctx
    _active_user(s)
    with _embedder() as embedder:
        run_cycle(s, embedder=embedder, expected_embedder=EXPECTED,
                  source_configs=_sources(remotive={}),
                  run_source=lambda *a: [_link(snippet="A real description. " * 30)])
    s.flush()

    job = s.scalars(CorpusJob.__table__.select().order_by(CorpusJob.id.desc()).limit(1)).first()
    assert job is not None


# ── the budget ────────────────────────────────────────────────────────────────────────────────

def test_the_fan_out_budget_is_respected(ctx):
    """Unbounded, 50 users × 5 terms × 3 sources is 750 queries a cycle — a ban and a bill."""
    s = ctx
    for i in range(10):
        _active_user(s, keywords=[f"u{i}term{j}" for j in range(5)])

    report, deferred = run_cycle(s, source_configs={}, max_queries=7,
                                 run_source=lambda *a: [])
    assert report.queries_planned == 7
    assert report.queries_deferred > 0
    assert len(deferred) == report.queries_deferred


def test_deferred_queries_are_returned_for_the_next_cycle(ctx):
    """Advice for next time, not persisted state — a stored plan would have to be reconciled
    against configs that have since changed."""
    s = ctx
    for i in range(4):
        _active_user(s, keywords=[f"u{i}a", f"u{i}b"])

    _, deferred = run_cycle(s, source_configs={}, max_queries=3, run_source=lambda *a: [])
    assert deferred

    report2, _ = run_cycle(s, source_configs={}, max_queries=3,
                           carried_over=deferred, run_source=lambda *a: [])
    assert report2.queries_planned == 3


# ── who is in the fold ────────────────────────────────────────────────────────────────────────

def test_a_dormant_user_costs_nothing(ctx):
    """G11's whole point: an abandoned account must stop consuming harvest budget."""
    s = ctx
    active = _active_user(s, keywords=["activeterm"])
    dormant = _active_user(s, keywords=["dormantterm"])
    dormant.status, dormant.active = "dormant", False
    s.flush()

    report, _ = run_cycle(s, source_configs={}, run_source=lambda *a: [])
    assert report.queries_planned >= 1
    assert active.id and dormant.id


def test_an_unverified_signup_consumes_no_harvest_budget(ctx):
    """TENANT_SAFETY §5: a signup flood must cost nothing."""
    s = ctx
    pending = _active_user(s)
    pending.status, pending.active = "pending_verification", False
    s.flush()

    report, _ = run_cycle(s, source_configs={}, run_source=lambda *a: [])
    assert report.queries_planned == 0


# ── failure containment ───────────────────────────────────────────────────────────────────────

def test_one_dead_source_cannot_sink_the_cycle(ctx):
    """Sources break upstream constantly. A cycle that aborts on the first failure delivers
    nothing to anybody."""
    s = ctx
    _active_user(s)
    good = _link()

    def flaky(source, cfg, repo):
        if source.key == "remotive":
            raise RuntimeError("upstream changed its JSON again")
        return [good]

    with _embedder() as embedder:
        report, _ = run_cycle(s, embedder=embedder, expected_embedder=EXPECTED,
                              source_configs=_sources(remotive={}, himalayas={}),
                              run_source=flaky)
    s.flush()

    assert "remotive" in report.source_failures
    assert report.new_jobs == 1, "the healthy source still delivered"


# ── overlap ───────────────────────────────────────────────────────────────────────────────────

def test_two_cycles_cannot_run_at_once(ctx):
    """A timer firing while the previous run is still going must SKIP, not queue.

    Queueing turns one slow cycle into a backlog all doing the same work, and the fan-out budget is
    per cycle — two at once spends it twice, against sources that are already grey-area.
    """
    s = ctx
    _active_user(s)

    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as other_conn:
        other = Session(bind=other_conn)
        with harvest_lock(other) as held:
            assert held, "the fixture failed to take the lock"
            report, _ = run_cycle(s, source_configs={}, run_source=lambda *a: [])
        other.close()

    assert report.skipped_locked is True
    assert report.queries_planned == 0


def test_the_lock_is_released_after_a_cycle(ctx):
    """Otherwise the first run wedges every subsequent one and harvesting stops silently."""
    s = ctx
    run_cycle(s, source_configs={}, run_source=lambda *a: [])

    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as conn:
        other = Session(bind=conn)
        with harvest_lock(other) as held:
            assert held, "the lock was not released"
        other.close()
