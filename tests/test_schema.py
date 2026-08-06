"""Schema invariants that the design documents treat as non-negotiable."""
from __future__ import annotations

import os
import uuid

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from libs.db.models import (
    CorpusJob,
    QueueJob,
    User,
    UserConfig,
    UserJobState,
    UserLabel,
    UserSecret,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set (no live Postgres)")


@pytest.fixture()
def session():
    """A session whose work is always rolled back, so tests cannot leave rows behind."""
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


def _user(session, email: str | None = None) -> User:
    u = User(
        email=email or f"{uuid.uuid4()}@example.test",
        password_hash="argon2-placeholder",
        timezone="Europe/Budapest",
    )
    session.add(u)
    session.flush()
    return u


def _job(session, source: str = "greenhouse") -> CorpusJob:
    token = uuid.uuid4().hex
    j = CorpusJob(
        url=f"https://example.test/{token}",
        canonical_url=f"https://example.test/{token}",
        content_key=token[:32],
        source=source,
        title="Senior Backend Engineer",
    )
    session.add(j)
    session.flush()
    return j


def test_deleting_a_user_removes_every_per_user_row(session):
    """Account deletion is one statement, correct by construction (PLATFORM_ARCHITECTURE.md §8)."""
    u = _user(session)
    j = _job(session)
    session.add_all([
        UserConfig(user_id=u.id, config={"keywords": ["go"]}),
        UserSecret(user_id=u.id, kind="telegram_bot", ciphertext=b"x", nonce=b"y", last_4="1234"),
        UserJobState(user_id=u.id, job_id=j.id, score=0.9),
        UserLabel(user_id=u.id, job_id=j.id, label=1),
        QueueJob(lane="train", user_id=u.id, dedup_key="retrain", payload={}),
    ])
    session.flush()

    session.delete(u)
    session.flush()

    for model in (UserConfig, UserSecret, UserJobState, UserLabel, QueueJob):
        remaining = session.scalar(
            select(func.count()).select_from(model).where(model.user_id == u.id))
        assert remaining == 0, f"{model.__tablename__} left {remaining} orphan row(s)"


def test_deleting_a_user_does_not_touch_the_shared_corpus(session):
    """The corpus holds public postings only, so it must survive its users."""
    u = _user(session)
    j = _job(session)
    session.add(UserJobState(user_id=u.id, job_id=j.id, score=0.5))
    session.flush()

    session.delete(u)
    session.flush()

    assert session.get(CorpusJob, j.id) is not None


def test_a_corpus_row_cannot_be_deleted_out_from_under_user_history(session):
    """`applied_at` is the record a user would be angriest to lose (PLATFORM_ARCHITECTURE.md §4)."""
    u = _user(session)
    j = _job(session)
    session.add(UserJobState(user_id=u.id, job_id=j.id, applied_at=func.now()))
    session.flush()

    with pytest.raises(IntegrityError):
        session.delete(j)
        session.flush()


def test_duplicate_pending_work_collapses_to_one_row(session):
    """Five labels must produce one retrain, not five (SCHEDULER_PLAN.md §3)."""
    u = _user(session)
    session.add(QueueJob(lane="train", user_id=u.id, dedup_key="retrain", payload={}))
    session.flush()

    with pytest.raises(IntegrityError):
        session.add(QueueJob(lane="train", user_id=u.id, dedup_key="retrain", payload={}))
        session.flush()


def test_a_finished_job_stops_blocking_the_next_one(session):
    """The dedup index is partial on ('ready','claimed') for a reason."""
    u = _user(session)
    done = QueueJob(lane="train", user_id=u.id, dedup_key="retrain", payload={}, state="done")
    session.add(done)
    session.flush()

    session.add(QueueJob(lane="train", user_id=u.id, dedup_key="retrain", payload={}))
    session.flush()   # must not raise


def test_the_delivery_cadence_is_one_platform_setting(session):
    """It was a bounded per-user column."""
    from libs import settings

    assert settings.get(session, "delivery_interval_min") == settings.DELIVERY_INTERVAL_MIN

    settings.put(session, "delivery_interval_min", 30, by="operator@example.com")
    session.flush()
    assert settings.get(session, "delivery_interval_min") == 30


def test_only_one_model_artifact_can_be_active_per_user(session):
    """Enforced by a partial unique index, not by the trainer clearing the old flag first."""
    from libs.db.models import UserModel

    u = _user(session)
    common = {
        "feature_schema_version": 1,
        "profile_generation": 0,
        "embedder_model": "BAAI/bge-base-en-v1.5",
        "embedder_dim": 768,
        "embedder_version": "1.9",
        "is_active": True,
    }
    session.add(UserModel(user_id=u.id, version=1, path="/models/a.joblib", **common))
    session.flush()

    with pytest.raises(IntegrityError):
        session.add(UserModel(user_id=u.id, version=2, path="/models/b.joblib", **common))
        session.flush()


def test_every_timestamp_column_is_timezone_aware(session):
    """G7: per-day counters reset at the user's local midnight, which naive timestamps cannot express."""
    naive = session.execute(text("""
        SELECT table_name, column_name FROM information_schema.columns
        WHERE table_schema = 'public'
          AND data_type LIKE 'timestamp%%'
          AND data_type NOT LIKE '%%with time zone'
    """)).fetchall()
    assert naive == [], f"naive timestamp columns: {naive}"


def test_an_unknown_setting_key_is_refused_rather_than_stored(session):
    """A typo that persists is a setting that silently never applies."""
    import pytest as _pytest

    from libs import settings

    with _pytest.raises(KeyError):
        settings.put(session, "freshnes_hours", 4)
    with _pytest.raises(KeyError):
        settings.get(session, "freshnes_hours")


def test_freshness_is_per_source_and_falls_back(session):
    """Sites differ in how fast they list and how far back their filters reach."""
    from libs import settings

    assert settings.freshness_hours(session, "linkedin") == 4
    assert settings.freshness_hours(session, "some-new-board") == settings.DEFAULT_FRESHNESS_HOURS

    settings.put(session, "source_freshness_hours", {"linkedin": 2, "arbeitnow": 8})
    session.flush()

    assert settings.freshness_hours(session, "linkedin") == 2
    assert settings.freshness_hours(session, "arbeitnow") == 8

    # What an operator stores is a set of overrides, not a replacement.
    assert settings.freshness_hours(session, "workday") == \
        settings.DEFAULT_SOURCE_FRESHNESS_HOURS["workday"]
    assert settings.freshness_hours(session, "workday") > settings.DEFAULT_FRESHNESS_HOURS
    assert settings.freshness_hours(session, "some-new-board") == settings.DEFAULT_FRESHNESS_HOURS
