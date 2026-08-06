"""Where the jobs are — and whether the page telling you can be trusted.

**A dashboard that reports zero when it means "I did not look" is worse than no dashboard**, because
it is reassuring. Every number here is a count of rows against the condition the *next* stage
actually reads, so a stage cannot show empty while work sits in it — and the tests below are mostly
about that property rather than about the arithmetic.

The motivating incident is one day old: 229 rows sat awaiting an embed for over an hour while both
health alerts pointed at delivery, because nothing anywhere reported the size of that queue
(ROADMAP 6ax, 6ba).
"""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("sqlalchemy")

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (no live Postgres)")


@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        conn.close()


def _job(session, **kwargs):
    from libs.db.models import CorpusJob

    row = CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test",
                    title="Backend Engineer", **kwargs)
    session.add(row)
    session.flush()
    return row


def stage(report: dict, key: str) -> dict:
    return next(s for s in report["stages"] if s["key"] == key)


def test_every_stage_of_the_pipeline_is_present(db):
    """Missing stages are the failure this exists to prevent: a stage nobody drew is a stage nobody
    watches, which is how two dead sweeps and a labeller that never started went unnoticed."""
    from libs.ops.pipeline import pipeline

    keys = [s["key"] for s in pipeline(db)["stages"]]
    assert keys == ["gather", "store", "describe", "embed", "score", "deliver"]
    for entry in pipeline(db)["stages"]:
        assert entry["label"] and entry["what"], "every stage says what it is, on the page itself"


def test_a_row_waiting_to_be_embedded_is_counted_as_waiting(db):
    """**The incident, in one assertion.** 229 rows described and unembedded, and no number
    anywhere said so."""
    from libs.ops.pipeline import pipeline

    _job(db, description="We build things. " * 40, description_fetched_at=datetime.now(UTC))
    db.flush()

    assert stage(pipeline(db), "embed")["waiting"] == 1


def test_a_row_with_no_description_waits_at_describe_not_at_embed(db):
    """Each stage counts what *it* is holding. A row that has not been described cannot be waiting
    to embed, and showing it in both would double the queue."""
    from libs.ops.pipeline import pipeline

    _job(db)
    db.flush()

    report = pipeline(db)
    assert stage(report, "describe")["waiting"] == 1
    assert stage(report, "embed")["waiting"] == 0


def test_a_trimmed_row_is_not_a_backlog(db):
    """Retention drops the description and vector after 90 days, deliberately. Counting those as
    waiting would make the sweep that keeps the disk flat look like a stalled stage."""
    from libs.ops.pipeline import pipeline

    _job(db, trimmed_at=datetime.now(UTC))
    db.flush()

    assert stage(pipeline(db), "describe")["waiting"] == 0


def test_the_verdict_is_about_age_not_size(db):
    """**A thousand rows moving is fine; forty stuck for a day is not.** Colouring by size would
    turn a busy platform permanently red and teach its reader to ignore it."""
    from libs.ops.pipeline import pipeline

    for _ in range(50):
        _job(db)
    db.flush()

    assert stage(pipeline(db), "describe")["verdict"] == "ok", "fresh, however many"


def test_a_stage_holding_something_old_turns_red(db):
    """The other half: one row nobody has picked up for a day is the signal, and it is the shape
    every outage here has had."""
    from libs.ops.pipeline import pipeline

    old = _job(db)
    old.first_seen_at = datetime.now(UTC) - timedelta(days=1)
    db.flush()

    entry = stage(pipeline(db), "describe")
    assert entry["verdict"] == "down"
    assert entry["oldest_min"] > 60


def test_an_empty_stage_is_green_and_says_nothing_about_age(db):
    """`None` rather than zero. Zero minutes is a claim about a queue that does not exist."""
    from libs.ops.pipeline import pipeline

    entry = stage(pipeline(db), "embed")
    assert entry["waiting"] == 0
    assert entry["oldest_min"] is None
    assert entry["verdict"] == "ok"


def test_the_thresholds_come_from_the_tick_that_clears_them(db):
    """Derived, not chosen — the harvest interval is what is supposed to drain these stages, so a
    change to it moves the colours with it."""
    from libs.ops.pipeline import pipeline
    from libs.settings import PLATFORM_TICK_MIN

    report = pipeline(db)
    assert report["amber_after_min"] == 2 * PLATFORM_TICK_MIN
    assert report["red_after_min"] == 8 * PLATFORM_TICK_MIN


def test_scoring_counts_work_not_postings(db):
    """One corpus row waiting for three accounts is three units of work. Counting distinct postings
    would understate the queue by exactly the number of users, which is the number that matters as
    this grows."""
    from libs.db.models import User
    from libs.ops.pipeline import pipeline

    for _ in range(2):
        user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active",
                    active=True, model_version=1)
        db.add(user)
    db.flush()
    _job(db, description="x " * 200, embedding=b"\x00" * 8, embedding_dim=2,
         embedding_version="v1", text_sha256=uuid.uuid4().hex * 2,
         posted_at=datetime.now(UTC))
    db.flush()

    assert stage(pipeline(db), "score")["waiting"] >= 2
