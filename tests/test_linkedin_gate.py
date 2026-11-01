"""LinkedIn jobs for everyone, and the LinkedIn tier's global governor."""
from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta

from libs.db import insert, now
from libs.db.models import CorpusJob, User
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


# ── LinkedIn is everyone's ────────────────────────────────────────────────────────────────────

def test_linkedin_jobs_reach_everyone_their_words_match(db):
    """Fetched through the site's proxy since 2026-11-01, so nobody needs one of their own to see them.
    Before, a person without a proxy never saw a LinkedIn job (decision D3, retired)."""
    first, second = _user(db), _user(db)
    linkedin_job, board_job = _job(db, "linkedin"), _job(db, "greenhouse")

    for person in (first, second):
        assert {linkedin_job, board_job} <= {job.id for job in _feed(db, person)}


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
