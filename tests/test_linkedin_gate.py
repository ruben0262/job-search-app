"""The LinkedIn visibility gate and the tier's global governor."""
from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from libs.db import insert, now
from libs.db.models import CorpusJob, ProxyCredential, User
from libs.matching import matching_jobs
from libs.tenant.repository import TenantRepository
from services.harvester.governor import (
    LINKEDIN_JITTER_FRACTION,
    LINKEDIN_MIN_INTERVAL_MIN,
    linkedin_enabled,
    next_linkedin_run,
    resolve_linkedin_interval,
)


def _user(db) -> User:
    """An account searching for "backend engineer", which every job below matches."""
    user = insert(db, User(email=f"{uuid.uuid4()}@example.test", password_hash="x",
                           timezone="Europe/Budapest", status="active", active=True))
    TenantRepository(db).save_config(user.id, {"sections": [
        {"platform": "all", "keywords": ["backend engineer"]}]}, schema_version=2)
    return user


def _job(db, source: str, *, minutes_ago: int = 1) -> int:
    moment = now() - timedelta(minutes=minutes_ago)
    return insert(db, CorpusJob(url=f"https://example.test/{uuid.uuid4().hex}", source=source,
                                title="Backend Engineer", company=f"Acme{uuid.uuid4().hex[:8]}",
                                description="Go, gRPC, Postgres. " * 20, posted_at=moment,
                                first_seen_at=moment)).id


def _feed(db, user, **kwargs) -> list[CorpusJob]:
    return [match.job for match in matching_jobs(db, user, **kwargs)]


def _proxy(db, user, status: str) -> None:
    insert(db, ProxyCredential(user_id=user.id, secret_ref="secret:proxy", status=status))


# ── the gate ──────────────────────────────────────────────────────────────────────────────────

def test_a_user_without_a_credential_sees_no_linkedin_rows(db):
    """The free-rider case, and the reason the gate exists at all."""
    user = _user(db)
    linkedin_job = _job(db, "linkedin")
    ats_job = _job(db, "greenhouse")

    assert TenantRepository(db).linkedin_visible(user.id) is False
    feed_ids = [j.id for j in _feed(db, user)]
    assert linkedin_job not in feed_ids, "a paid-for row leaked to a user who did not pay"
    assert ats_job in feed_ids, "the free two-thirds must still be a useful product"


def test_a_healthy_credential_unlocks_the_tier(db):
    user = _user(db)
    _proxy(db, user, "healthy")
    linkedin_job = _job(db, "linkedin")

    assert TenantRepository(db).linkedin_visible(user.id) is True
    assert linkedin_job in [j.id for j in _feed(db, user)]


@pytest.mark.parametrize("status", ["quarantined", "disabled"])
def test_an_unhealthy_credential_closes_the_gate_again(db, status):
    """Quarantine must actually stop delivery, or a broken credential becomes free access."""
    user = _user(db)
    _proxy(db, user, status)
    linkedin_job = _job(db, "linkedin")

    assert TenantRepository(db).linkedin_visible(user.id) is False
    assert linkedin_job not in [j.id for j in _feed(db, user)]


def test_the_gate_is_applied_before_the_limit_not_after(db):
    """Filtering after the scan would hand short pages to exactly the users who have no credential."""
    user = _user(db)
    # The four newest are LinkedIn's, so a gate applied after the scan would leave nothing.
    for i in range(6):
        _job(db, "linkedin" if i < 4 else "greenhouse", minutes_ago=i + 1)

    feed = _feed(db, user, scan=2)
    assert len(feed) == 2, "the page is filled from eligible rows, not truncated by the gate"
    assert all(j.source != "linkedin" for j in feed)


def test_the_gate_is_per_user_on_the_same_shared_row(db):
    """One corpus row, two users, different visibility. That is the whole design in one assertion."""
    paying, freeloader = _user(db), _user(db)
    _proxy(db, paying, "healthy")
    shared_linkedin_job = _job(db, "linkedin")

    assert shared_linkedin_job in [j.id for j in _feed(db, paying)]
    assert shared_linkedin_job not in [j.id for j in _feed(db, freeloader)]


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
