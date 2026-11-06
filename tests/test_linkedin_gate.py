"""LinkedIn jobs for everyone, and the LinkedIn tier's global governor."""
from __future__ import annotations

import uuid
from datetime import timedelta

from libs.db import insert, now
from libs.db.models import CorpusJob, User
from libs.matching import matching_jobs
from libs.tenant.repository import TenantRepository
from services.harvester.governor import linkedin_enabled


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
                                first_seen_at=moment, described_at=moment)).id


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

def test_the_kill_switch_stops_the_tier(monkeypatch):
    """During an incident the alternative is editing 50 users' configs while LinkedIn blocks."""
    monkeypatch.delenv("HARVEST_LINKEDIN_DISABLED", raising=False)
    assert linkedin_enabled() is True

    for value in ("1", "true", "YES"):
        monkeypatch.setenv("HARVEST_LINKEDIN_DISABLED", value)
        assert linkedin_enabled() is False, f"{value!r} must disable the tier"

    monkeypatch.setenv("HARVEST_LINKEDIN_DISABLED", "0")
    assert linkedin_enabled() is True, "an explicit 0 must not disable it"
