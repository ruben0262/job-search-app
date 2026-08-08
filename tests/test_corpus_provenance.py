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


def test_the_linkedin_ingest_names_the_account_it_ran_for():
    """The wiring itself: a pass that does not pass the owner through records nothing."""
    import inspect

    from services.harvester import linkedin

    source = inspect.getsource(linkedin)

    assert "_ingest(corpus, links, user_id=user.id)" in source, (
        "the LinkedIn pass must tell the ingest whose harvest it is")
