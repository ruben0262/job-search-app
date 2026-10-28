"""Which jobs a person sees: green words pick them, red words veto them, the order is explainable."""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from libs.matching import TEXT_POINTS, TITLE_POINTS, green_words, pattern, score

needs_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"),
                              reason="DATABASE_URL not set (no live Postgres)")


def test_a_green_word_matches_whole_words_and_ignores_case_spaces_and_hyphens():
    regex = pattern(green_words({"sections": [{"keywords": ["Java", "front end"]}]}))
    assert regex.search("Senior JAVA developer")
    assert regex.search("Front-End Engineer")
    assert not regex.search("JavaScript developer")


def test_a_title_match_outranks_a_text_match_and_says_which_words_matched():
    regex = pattern(["backend engineer", "python"])
    assert score(regex, "Backend Engineer", "") == (TITLE_POINTS, ("backend engineer",))
    assert score(regex, "Platform role", "We use Python daily") == (TEXT_POINTS, ("python",))
    assert score(regex, "Backend-Engineer", "Python") == (100, ("backend engineer", "python"))
    assert score(regex, "Nurse", "Ward rounds") == (0, ())


@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from libs.db.models import User, UserConfig

    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active")
    session.add(user)
    session.flush()
    session.add(UserConfig(user_id=user.id, schema_version=2, config={"sections": [{
        "platform": "all", "keywords": ["backend engineer"], "exclude_keywords": ["senior"]}]}))
    try:
        yield session, user
    finally:
        session.close()
        trans.rollback()
        conn.close()


def _jobs(session, *rows):
    from libs.db.models import CorpusJob

    now = datetime.now(UTC)
    jobs = [CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test", title=title,
                      description=text, is_remote=remote, posted_at=now,
                      first_seen_at=now - timedelta(minutes=age))
            for title, text, remote, age in rows]
    session.add_all(jobs)
    session.flush()
    return jobs


@needs_db
def test_green_words_pick_jobs_red_words_veto_them_and_titles_rank_first(db):
    from libs.matching import matching_jobs

    session, user = db
    _jobs(session,
          ("Platform Engineer", "You are a backend engineer at heart", True, 1),
          ("Backend Engineer", "Build APIs", True, 2),
          ("Senior Backend Engineer", "Lead the team", True, 3),
          ("Registered Nurse", "Ward rounds", True, 4))

    found = matching_jobs(session, user)

    assert [(m.job.title, m.score) for m in found] == [
        ("Backend Engineer", TITLE_POINTS), ("Platform Engineer", TEXT_POINTS)]
    assert found[0].words == ("backend engineer",)


@needs_db
def test_since_only_returns_jobs_first_seen_after_it(db):
    from libs.matching import matching_jobs

    session, user = db
    _old, new = _jobs(session, ("Backend Engineer", "", True, 60), ("Backend Engineer II", "", True, 1))

    found = matching_jobs(session, user, since=datetime.now(UTC) - timedelta(minutes=30))

    assert [m.job.id for m in found] == [new.id]


@needs_db
def test_remote_only_drops_jobs_known_to_be_on_site_but_keeps_unknown_ones(db):
    from libs.db.models import UserConfig
    from libs.matching import matching_jobs

    session, user = db
    config = session.get(UserConfig, user.id)
    config.config = {"sections": [{"platform": "all", "keywords": ["backend engineer"],
                                   "remote_policy": "remote_only"}]}
    _jobs(session, ("Backend Engineer A", "", False, 1), ("Backend Engineer B", "", None, 2))

    assert [m.job.title for m in matching_jobs(session, user)] == ["Backend Engineer B"]


@needs_db
def test_no_green_words_means_no_jobs_rather_than_every_job(db):
    from libs.db.models import UserConfig
    from libs.matching import matching_jobs

    session, user = db
    session.get(UserConfig, user.id).config = {"sections": [{"platform": "all", "keywords": []}]}
    _jobs(session, ("Backend Engineer", "", True, 1))

    assert matching_jobs(session, user) == []
