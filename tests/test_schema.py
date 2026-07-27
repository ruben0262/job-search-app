"""Schema invariants that the design documents treat as non-negotiable.

The seed of T1 (`TEST_STRATEGY.md`), written now because the roadmap says the isolation test lands
as soon as the first per-user table exists — not once the repository layer is built on top of it.
What is here proves the *database's* guarantees; the query-level half of T1 (two users, overlapping
jobs, every read path returns only the caller's rows) arrives with the repository layer.

Needs a live Postgres. Set ``DATABASE_URL`` and run; without it these skip, exactly as the
embedder's integration tier does::

    DATABASE_URL=postgresql+psycopg://jobsearch:<pw>@127.0.0.1:5433/jobsearch pytest tests/

Each test runs inside a transaction that is rolled back, so the database is left as it was found.
"""
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
    """Account deletion is one statement, correct by construction (PLATFORM_ARCHITECTURE.md §8).

    If this fails, deletion silently leaves orphaned credentials and labels behind — which is both a
    GDPR problem and the exact failure the cascade design exists to make impossible.
    """
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
    """The corpus holds public postings only, so it must survive its users.

    This is the other half of the §8 guarantee and the reason no user data may ever be denormalised
    onto a corpus row.
    """
    u = _user(session)
    j = _job(session)
    session.add(UserJobState(user_id=u.id, job_id=j.id, score=0.5))
    session.flush()

    session.delete(u)
    session.flush()

    assert session.get(CorpusJob, j.id) is not None


def test_a_corpus_row_cannot_be_deleted_out_from_under_user_history(session):
    """`applied_at` is the record a user would be angriest to lose (PLATFORM_ARCHITECTURE.md §4).

    Retention nulls `description`/`embedding` and keeps the row; acted-on rows are pinned. RESTRICT
    is the backstop for a DELETE that should never have been written — it must fail loudly rather
    than quietly taking someone's applied history with it.
    """
    u = _user(session)
    j = _job(session)
    session.add(UserJobState(user_id=u.id, job_id=j.id, applied_at=func.now()))
    session.flush()

    with pytest.raises(IntegrityError):
        session.delete(j)
        session.flush()


def test_duplicate_pending_work_collapses_to_one_row(session):
    """Five labels must produce one retrain, not five (SCHEDULER_PLAN.md §3).

    The debounce is a partial unique index rather than application logic, because application logic
    that checks-then-inserts races with itself under concurrency.
    """
    u = _user(session)
    session.add(QueueJob(lane="train", user_id=u.id, dedup_key="retrain", payload={}))
    session.flush()

    with pytest.raises(IntegrityError):
        session.add(QueueJob(lane="train", user_id=u.id, dedup_key="retrain", payload={}))
        session.flush()


def test_a_finished_job_stops_blocking_the_next_one(session):
    """The dedup index is partial on ('ready','claimed') for a reason.

    If it covered every state, a completed retrain would permanently prevent the next one being
    enqueued — the user's model would silently freeze after its first training run.
    """
    u = _user(session)
    done = QueueJob(lane="train", user_id=u.id, dedup_key="retrain", payload={}, state="done")
    session.add(done)
    session.flush()

    session.add(QueueJob(lane="train", user_id=u.id, dedup_key="retrain", payload={}))
    session.flush()   # must not raise


def test_notify_interval_is_bounded_by_the_database(session):
    """TENANT_SAFETY.md §4 bounds this 15..1440. A 1-minute interval is a self-inflicted ban.

    The Pydantic schema validates it too; this asserts the database is the authority rather than
    trusting that every write path went through the form.
    """
    u = _user(session)
    u.notify_interval_min = 1
    with pytest.raises(IntegrityError):
        session.flush()


def test_only_one_model_artifact_can_be_active_per_user(session):
    """Enforced by a partial unique index, not by the trainer clearing the old flag first.

    Two active artifacts means the scorer's choice of which to load is undefined, and the symptom is
    scores that change depending on which container answered.
    """
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
    """G7: per-day counters reset at the user's local midnight, which naive timestamps cannot express.

    Asserted against the live schema rather than the models, because it is the database that has to
    be right — and a naive column added later would otherwise go unnoticed until a quota reset at the
    wrong hour for someone in another timezone.
    """
    naive = session.execute(text("""
        SELECT table_name, column_name FROM information_schema.columns
        WHERE table_schema = 'public'
          AND data_type LIKE 'timestamp%%'
          AND data_type NOT LIKE '%%with time zone'
    """)).fetchall()
    assert naive == [], f"naive timestamp columns: {naive}"
