"""The feedback loop: what an action records. Switched off (CLAUDE.md §3), and still tested.

These ran through the per-user bot's webhook until 2026-10-28. Its buttons are gone — a job card now
carries plain links — so the tests call `libs.feedback` directly, which is what they were testing.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("sqlalchemy")

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (no live Postgres)")


@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from libs.db.models import CorpusJob, User, UserJobState, UserProfileFile
    from libs.profiles import GENERATION_FILES

    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")

    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active",
                profile_generation=1, profile_version="abc12345")
    session.add(user)
    session.flush()
    for name in GENERATION_FILES:
        session.add(UserProfileFile(user_id=user.id, name=name, kind="context",
                                    content=f"Real content for {name}, with no placeholders."))
    user.profile = {"identity": {"name": "Alice Example"}, "confirmed": True}

    job = CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test", title="Engineer")
    session.add(job)
    session.flush()
    session.add(UserJobState(user_id=user.id, job_id=job.id, score=80.0))
    session.flush()
    try:
        yield session, user, job
    finally:
        session.close()
        trans.rollback()
        conn.close()


def record(session, user, job_id, action="applied"):
    from libs.feedback import record as record_action

    result = record_action(session, user.id, job_id, action)
    session.flush()
    return result


# ── what an action records ────────────────────────────────────────────────────────────────────

def test_applying_writes_a_feedback_label_worth_five_llm_labels(db):
    """`source='feedback'` and `weight=5` have been in the schema since Phase 2 with nothing writing them."""
    from libs.db.models import UserLabel
    from libs.feedback import APPLIED_LABEL, FEEDBACK_WEIGHT

    session, user, job = db
    assert record(session, user, job.id).changed

    label = session.get(UserLabel, (user.id, job.id))
    assert (label.label, label.source, label.weight) == (APPLIED_LABEL, "feedback", FEEDBACK_WEIGHT)
    assert label.profile_generation == user.profile_generation


def test_applying_is_ninety_not_one_hundred():
    """People apply to things they are unsure about."""
    from libs.feedback import APPLIED_LABEL, SKIPPED_LABEL

    assert 0 < SKIPPED_LABEL < APPLIED_LABEL < 100


def test_feedback_overwrites_the_model_guess_for_that_job(db):
    """The whole point: an LLM label for this job almost certainly exists already."""
    from libs.db.models import UserLabel

    session, user, job = db
    session.add(UserLabel(user_id=user.id, job_id=job.id, label=45, source="llm", weight=1.0,
                          profile_generation=1))
    session.flush()

    record(session, user, job.id)

    label = session.get(UserLabel, (user.id, job.id))
    assert (label.label, label.source) == (90, "feedback")


def test_a_second_action_does_not_move_the_date_somebody_applied(db):
    """`applied_at` is the durable record of a real event."""
    from libs.db.models import UserJobState

    session, user, job = db
    record(session, user, job.id)
    first = session.get(UserJobState, (user.id, job.id)).applied_at

    assert not record(session, user, job.id).changed
    assert session.get(UserJobState, (user.id, job.id)).applied_at == first


def test_skipping_dismisses_the_job_and_labels_it_low(db):
    from libs.db.models import UserJobState, UserLabel

    session, user, job = db
    record(session, user, job.id, "skipped")

    assert session.get(UserJobState, (user.id, job.id)).dismissed_at is not None
    assert session.get(UserLabel, (user.id, job.id)).label == 10


def test_an_action_on_a_job_the_user_never_had_is_not_an_error(db):
    """The message may be old and the row trimmed. Not worth raising at somebody, never silent."""
    session, user, _ = db
    assert not record(session, user, 99_999_999).changed


def test_any_action_counts_as_engagement(db):
    session, user, job = db
    assert user.last_active_at is None

    record(session, user, job.id)
    assert user.last_active_at is not None


# ── which job to ask about ────────────────────────────────────────────────────────────────────

def _scored(session, user, *, score, description="a real description " * 30, model_version=None):
    from libs.db.models import CorpusJob, UserJobState

    job = CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test",
                    title="Engineer", description=description)
    session.add(job)
    session.flush()
    session.add(UserJobState(user_id=user.id, job_id=job.id, score=score,
                             scored_model_version=user.model_version
                             if model_version is None else model_version))
    session.flush()
    return job


def test_the_job_to_ask_about_is_the_one_closest_to_the_delivery_threshold(db):
    """The jobs where an answer teaches most are the ones the model is least sure about."""
    from libs.feedback import next_to_rate

    session, user, _ = db
    _scored(session, user, score=98)
    boundary = _scored(session, user, score=59)
    _scored(session, user, score=12)

    assert next_to_rate(session, user.id) == boundary.id


def test_the_pool_includes_jobs_that_were_never_delivered(db):
    """The property that makes asking worth building."""
    from libs.feedback import rating_pool

    session, user, _ = db
    below = _scored(session, user, score=55)
    above = _scored(session, user, score=65)

    pool = rating_pool(session, user.id)
    assert below.id in pool and above.id in pool


def test_a_job_with_no_real_description_is_never_offered(db):
    """The user would be judging a job title, and so would the model they are teaching."""
    from libs.feedback import rating_pool

    session, user, _ = db
    thin = _scored(session, user, score=60, description="Engineer wanted")
    assert thin.id not in rating_pool(session, user.id)


def test_a_job_scored_by_an_older_model_is_not_offered(db):
    """A score from a previous model says nothing about where the current model's boundary is."""
    from libs.feedback import rating_pool

    session, user, _ = db
    stale = _scored(session, user, score=60, model_version=(user.model_version or 0) + 5)
    assert stale.id not in rating_pool(session, user.id)


def test_a_job_already_answered_is_not_asked_about_again(db):
    """Asking twice reads as the platform not listening."""
    from libs.feedback import rating_pool

    session, user, _ = db
    job = _scored(session, user, score=60)
    assert job.id in rating_pool(session, user.id)

    record(session, user, job.id, "skipped")
    assert job.id not in rating_pool(session, user.id)


def test_nothing_left_to_ask_about_is_none_rather_than_an_error(db):
    from libs.feedback import next_to_rate

    session, user, _ = db
    assert next_to_rate(session, user.id) is None


# ── building a document ───────────────────────────────────────────────────────────────────────

def test_the_admission_check_and_the_worker_ask_the_same_question(db):
    """One definition. Two would drift again, and drifting is what caused the outage.

    The condition is "has the setup chat written this account's files", and nothing else. It used to
    be that plus a confirmed `user.profile`, asked in two places with different answers — the handler
    checked the files and promised a document, the worker checked the schema and refused.
    """
    from sqlalchemy import delete

    from libs.db.models import UserProfileFile
    from libs.profiles.repository import ProfileFiles
    from services.docgen import run

    session, user, job = db
    session.execute(delete(UserProfileFile).where(UserProfileFile.user_id == user.id))
    # A description, so the worker cannot refuse for that reason instead and pass this test by luck.
    job.description = "Backend engineer. Python, Postgres."
    session.flush()

    assert ProfileFiles(session, user.id).build_blocker() is not None
    # The worker's own refusal path must agree with what an admission check tells the user.
    with pytest.raises(run.NotBuildable, match="setup"):
        run.run_ai(session, user.id, job.id, kind="cv", client=None, model="any")


def test_the_document_is_named_from_the_name_the_user_typed():
    """`users.full_name` is hard data; the capture schema was a second copy of it."""
    from services.docgen.run import _filename

    assert _filename("Andras Lekli") == "Andras_Lekli_CV.pdf"
    assert _filename("Andras Lekli", "cover") == "Andras_Lekli_Cover.pdf"
    # Nullable, and a build must not fail over a filename.
    assert _filename(None).endswith(".pdf")
