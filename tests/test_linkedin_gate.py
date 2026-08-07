"""The LinkedIn visibility gate and the tier's global governor."""
from __future__ import annotations

import os
import random
import uuid
from datetime import UTC, datetime

import pytest

from services.harvester.governor import (
    LINKEDIN_JITTER_FRACTION,
    LINKEDIN_MIN_INTERVAL_MIN,
    linkedin_enabled,
    next_linkedin_run,
    resolve_linkedin_interval,
)

pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from libs.corpus.repository import CorpusRepository
from libs.db.models import ProxyCredential, User
from libs.tenant.repository import TenantRepository

db_required = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set (no live Postgres)")


@pytest.fixture()
def ctx():
    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    s = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield TenantRepository(s), CorpusRepository(s), s
    finally:
        s.close()
        trans.rollback()
        conn.close()


def _user(s) -> User:
    u = User(email=f"{uuid.uuid4()}@example.test", password_hash="x", timezone="Europe/Budapest")
    s.add(u)
    s.flush()
    return u


def _job(corpus, s, source: str) -> int:
    job_id = corpus.add(url=f"https://example.test/{uuid.uuid4().hex}", source=source,
                        title="Backend Engineer", company=f"Acme{uuid.uuid4().hex[:8]}")
    corpus.set_description(job_id, "Go, gRPC, Postgres. " * 20)
    s.flush()
    return job_id


# ── the gate ──────────────────────────────────────────────────────────────────────────────────

@db_required
def test_a_user_without_a_credential_sees_no_linkedin_rows(ctx):
    """The free-rider case, and the reason the gate exists at all."""
    t, c, s = ctx
    user = _user(s)
    linkedin_job = _job(c, s, "linkedin")
    ats_job = _job(c, s, "greenhouse")
    t.record_score(user.id, linkedin_job, score=0.99, model_version=1)
    t.record_score(user.id, ats_job, score=0.50, model_version=1)
    s.flush()

    assert t.linkedin_visible(user.id) is False
    feed_ids = [j.id for j in t.visible_feed(user.id)]
    assert linkedin_job not in feed_ids, "a paid-for row leaked to a user who did not pay"
    assert ats_job in feed_ids, "the free two-thirds must still be a useful product"


@db_required
def test_a_healthy_credential_unlocks_the_tier(ctx):
    t, c, s = ctx
    user = _user(s)
    s.add(ProxyCredential(user_id=user.id, secret_ref="secret:proxy", status="healthy"))
    linkedin_job = _job(c, s, "linkedin")
    t.record_score(user.id, linkedin_job, score=0.99, model_version=1)
    s.flush()

    assert t.linkedin_visible(user.id) is True
    assert linkedin_job in [j.id for j in t.visible_feed(user.id)]


@db_required
@pytest.mark.parametrize("status", ["quarantined", "disabled"])
def test_an_unhealthy_credential_closes_the_gate_again(ctx, status):
    """Quarantine must actually stop delivery, or a broken credential becomes free access."""
    t, c, s = ctx
    user = _user(s)
    s.add(ProxyCredential(user_id=user.id, secret_ref="secret:proxy", status=status))
    linkedin_job = _job(c, s, "linkedin")
    t.record_score(user.id, linkedin_job, score=0.99, model_version=1)
    s.flush()

    assert t.linkedin_visible(user.id) is False
    assert linkedin_job not in [j.id for j in t.visible_feed(user.id)]


@db_required
def test_the_gate_is_applied_before_the_limit_not_after(ctx):
    """Filtering after LIMIT would hand short pages to exactly the users who have no credential."""
    t, c, s = ctx
    user = _user(s)
    for i in range(6):
        job = _job(c, s, "linkedin" if i < 4 else "greenhouse")
        t.record_score(user.id, job, score=0.9 - i * 0.01, model_version=1)
    s.flush()

    feed = t.visible_feed(user.id, limit=2)
    assert len(feed) == 2, "the page is filled from eligible rows, not truncated by the gate"
    assert all(j.source != "linkedin" for j in feed)


@db_required
def test_the_gate_is_per_user_on_the_same_shared_row(ctx):
    """One corpus row, two users, different visibility. That is the whole design in one assertion."""
    t, c, s = ctx
    paying, freeloader = _user(s), _user(s)
    s.add(ProxyCredential(user_id=paying.id, secret_ref="secret:proxy", status="healthy"))
    shared_linkedin_job = _job(c, s, "linkedin")
    t.record_score(paying.id, shared_linkedin_job, score=0.9, model_version=1)
    t.record_score(freeloader.id, shared_linkedin_job, score=0.9, model_version=1)
    s.flush()

    assert shared_linkedin_job in [j.id for j in t.visible_feed(paying.id)]
    assert shared_linkedin_job not in [j.id for j in t.visible_feed(freeloader.id)]


# ── the governor ──────────────────────────────────────────────────────────────────────────────

def test_the_floor_never_overrides_what_an_operator_asked_for():
    """The floor was 60 and the panel's own default is 15."""
    from libs.limits import BY_KEY

    # The floor is this tier's own number — bandwidth and ban risk — not a delivery cadence.
    assert BY_KEY["linkedin_interval_min"].default == LINKEDIN_MIN_INTERVAL_MIN
    assert resolve_linkedin_interval(15) == 15
    assert resolve_linkedin_interval(240) == 240
    # Below the floor there is nothing to gain, so it still clamps.
    assert resolve_linkedin_interval(5) == LINKEDIN_MIN_INTERVAL_MIN


def test_jitter_stays_within_ten_percent_and_never_goes_backwards():
    """Bounded on both sides: a run scheduled in the past would fire immediately, forever."""
    now = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
    rng = random.Random(99)
    for _ in range(500):
        nxt = next_linkedin_run(now, 240, rng=rng)
        delta_min = (nxt - now).total_seconds() / 60
        assert 240 * (1 - LINKEDIN_JITTER_FRACTION) <= delta_min <= 240 * (1 + LINKEDIN_JITTER_FRACTION)
        assert nxt > now


def test_users_on_the_same_interval_do_not_fire_together():
    """The thundering herd per-user scheduling creates for free if nobody spreads it."""
    now = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
    rng = random.Random(7)
    times = {next_linkedin_run(now, 240, rng=rng) for _ in range(50)}
    assert len(times) > 45, "50 users on one interval must not collapse onto a handful of instants"


def test_scheduling_is_reproducible_with_a_seeded_rng():
    """A scheduler whose behaviour cannot be reproduced is one whose bugs cannot be either."""
    now = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
    a = [next_linkedin_run(now, 120, rng=random.Random(3)) for _ in range(5)]
    b = [next_linkedin_run(now, 120, rng=random.Random(3)) for _ in range(5)]
    assert a == b


def test_the_kill_switch_stops_the_tier(monkeypatch):
    """During an incident the alternative is editing 50 users' configs while LinkedIn blocks."""
    monkeypatch.delenv("HARVEST_LINKEDIN_DISABLED", raising=False)
    assert linkedin_enabled() is True

    for value in ("1", "true", "YES"):
        monkeypatch.setenv("HARVEST_LINKEDIN_DISABLED", value)
        assert linkedin_enabled() is False, f"{value!r} must disable the tier"

    monkeypatch.setenv("HARVEST_LINKEDIN_DISABLED", "0")
    assert linkedin_enabled() is True, "an explicit 0 must not disable it"
