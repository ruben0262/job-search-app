"""How a posting got into the corpus — recorded, so it never has to be inferred again.

On 2026-08-08 an account with no proxy was receiving LinkedIn mechanical-engineering postings.
Answering "how did those get here" took an afternoon and produced an inference rather than a fact:
the harvest logged `ran 66 quer(ies)` for the other account, that account's config held 22 keywords
across 3 locations, 22×3 is 66, and the proxy-less account's line every pass was `has no proxy
credential`. Correct, probably — and unreadable.

These tests are about the columns that make it readable.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("sqlalchemy")


@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set (no live Postgres)")

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


def test_a_row_records_the_search_term_that_found_it(db):
    """LinkedIn's matching is looser than its query, and that gap is the thing nobody could see."""
    from libs.corpus.repository import CorpusRepository
    from libs.db.models import CorpusJob

    job_id = CorpusRepository(db).add(
        url=f"https://www.linkedin.com/jobs/view/{uuid.uuid4()}", source="linkedin",
        title="Graduate Mechanical Design Engineer",
        discovered_via="graduate software engineer")
    db.flush()

    row = db.get(CorpusJob, job_id)
    assert row.discovered_via == "graduate software engineer"
    assert row.title != row.discovered_via, "the whole point is that these differ"


def test_the_per_user_tier_records_whose_harvest_paid_for_it(db):
    """LinkedIn is fetched on one account's own proxy, so it has exactly one owner."""
    from libs.corpus.repository import CorpusRepository
    from libs.db.models import CorpusJob, User

    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active")
    db.add(user)
    db.flush()

    job_id = CorpusRepository(db).add(
        url=f"https://www.linkedin.com/jobs/view/{uuid.uuid4()}", source="linkedin",
        title="Mechanical Design Engineer", discovered_by=user.id)
    db.flush()

    assert db.get(CorpusJob, job_id).discovered_by == user.id


def test_the_shared_cycle_leaves_the_owner_null(db):
    """One query folded across every account that wanted it has no single owner, and inventing one
    would be worse than saying so."""
    from libs.corpus.repository import CorpusRepository
    from libs.db.models import CorpusJob

    job_id = CorpusRepository(db).add(
        url=f"https://example.com/shared/{uuid.uuid4()}", source="himalayas",
        title="Backend Engineer", discovered_via="backend developer")
    db.flush()

    row = db.get(CorpusJob, job_id)
    assert row.discovered_by is None
    assert row.discovered_via == "backend developer", "the term is still known"


def test_deleting_an_account_does_not_delete_the_corpus(db):
    """Other accounts are scored against these rows, and a posting outlives whoever found it."""
    from libs.corpus.repository import CorpusRepository
    from libs.db.models import CorpusJob, User

    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active")
    db.add(user)
    db.flush()
    job_id = CorpusRepository(db).add(
        url=f"https://www.linkedin.com/jobs/view/{uuid.uuid4()}", source="linkedin",
        title="Engineer", discovered_by=user.id)
    db.flush()

    db.delete(user)
    db.flush()

    row = db.get(CorpusJob, job_id)
    db.refresh(row)
    assert row is not None, "the posting survives"
    assert row.discovered_by is None, "and forgets an owner who no longer exists"


def test_the_adapter_tags_every_link_with_its_query():
    """Tagged where the query and its results are both in scope, or it cannot be tagged at all."""
    from services.harvester.sources.base import JobLink

    link = JobLink(url="https://www.linkedin.com/jobs/view/3", title="Stress Engineer",
                   source="linkedin", found_via="graduate software engineer")

    assert link.as_corpus_row()["discovered_via"] == "graduate software engineer"


def test_a_feed_with_no_query_stores_null_rather_than_an_empty_string():
    """A feed source has no search term. NULL says that; "" reads as a term somebody typed."""
    from services.harvester.sources.base import JobLink

    link = JobLink(url="https://example.com/feed/1", title="Engineer", source="remoteok")

    assert link.as_corpus_row()["discovered_via"] is None


def test_the_linkedin_ingest_names_the_account_it_ran_for(db):
    """A pass that does not carry the owner through records nothing.

    Asserted by running the ingest and reading the row, not by grepping the module for a call.
    The first version of this test matched a literal line of source, which would have passed while
    the value was wrong and failed on a rename that changed nothing.
    """
    from libs.corpus.repository import CorpusRepository
    from libs.db.models import CorpusJob, User
    from services.harvester.linkedin import _ingest
    from services.harvester.sources.base import JobLink

    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active")
    db.add(user)
    db.flush()

    url = f"https://www.linkedin.com/jobs/view/{uuid.uuid4()}"
    new = _ingest(CorpusRepository(db),
                  [JobLink(url=url, title="Mechanical Design Engineer", source="linkedin",
                           found_via="graduate software engineer")],
                  user_id=user.id)
    db.flush()

    assert new == 1
    from sqlalchemy import select
    row = db.scalar(select(CorpusJob).where(CorpusJob.url == url))
    assert row.discovered_by == user.id, "whose proxy paid for it"
    assert row.discovered_via == "graduate software engineer", "and what returned it"


# ── abandoning a posting we cannot read ───────────────────────────────────────────────────────

def test_a_posting_is_abandoned_after_two_failed_reads(db):
    """Two, and it is never fetched, embedded or scored again.

    Before this the describer had no memory: a page that returned no usable text came back every
    fifteen minutes forever. Measured 2026-08-08, the same ~56 LinkedIn ids for a week, each
    attempt on a user's own metered residential proxy.
    """
    from sqlalchemy import select

    from libs.corpus.repository import (
        DESCRIBE_MAX_FAILURES,
        CorpusRepository,
        awaiting_description_conditions,
        awaiting_embedding_conditions,
    )
    from libs.db.models import CorpusJob

    corpus = CorpusRepository(db)
    job_id = corpus.add(url=f"https://www.linkedin.com/jobs/view/{uuid.uuid4()}",
                        source="linkedin", title="Unreadable Posting")
    db.execute(CorpusJob.__table__.update().where(CorpusJob.id == job_id)
               .values(description="a snippet only"))
    db.flush()

    assert corpus.record_describe_failure(job_id) == 1
    still_listed = db.scalars(
        select(CorpusJob.id).where(*awaiting_description_conditions())).all()
    assert job_id in still_listed, "one failure is a blip and deserves another IP"

    assert corpus.record_describe_failure(job_id) == DESCRIBE_MAX_FAILURES
    db.flush()

    assert job_id not in db.scalars(
        select(CorpusJob.id).where(*awaiting_description_conditions())).all()
    assert job_id not in db.scalars(
        select(CorpusJob.id).where(*awaiting_embedding_conditions())).all(), \
        "its only description is its own title; a vector for that is worse than none"


def test_a_successful_read_clears_the_count(db):
    """Consecutive means consecutive. A page that answers has not failed twice in a row."""
    from libs.corpus.repository import CorpusRepository
    from libs.db.models import CorpusJob

    corpus = CorpusRepository(db)
    job_id = corpus.add(url=f"https://www.linkedin.com/jobs/view/{uuid.uuid4()}",
                        source="linkedin", title="Intermittent Posting")
    db.flush()

    corpus.record_describe_failure(job_id)
    corpus.clear_describe_failures(job_id)
    db.flush()

    assert db.get(CorpusJob, job_id).describe_failures == 0


def test_an_abandoned_posting_is_never_scored_for_anybody(db):
    """The pre-score filter refuses it too, or a user's model is fitted against a job title."""
    from sqlalchemy import select

    from libs.corpus.repository import DESCRIBE_MAX_FAILURES, CorpusRepository
    from libs.db.models import CorpusJob
    from libs.tenant.filters import UserFilters, candidate_conditions

    corpus = CorpusRepository(db)
    job_id = corpus.add(url=f"https://www.linkedin.com/jobs/view/{uuid.uuid4()}",
                        source="linkedin", title="Unreadable Posting")
    db.flush()
    for _ in range(DESCRIBE_MAX_FAILURES):
        corpus.record_describe_failure(job_id)
    db.flush()

    filters = UserFilters.from_config(None, max_age_hours=24)
    assert job_id not in db.scalars(
        select(CorpusJob.id).where(*candidate_conditions(filters))).all()
