"""The front page and the jobs page, both on `libs.matching`: what a person sees, and why."""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("fastapi")

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (no live Postgres)")

# A stored config as accounts actually have them, retired field and all.
CONFIG = {"sections": [{"platform": "all", "keywords": ["backend engineer"]}],
          "max_notifications_per_day": 20}


@pytest.fixture()
def api():
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    os.environ["DISABLE_RATE_LIMIT"] = "1"

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from libs.auth.access import issue_access_token
    from libs.db.models import CorpusJob, User, UserConfig
    from services.api import deps
    from services.api.app import app

    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")

    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active",
                active=True, full_name="Alice Example", telegram_status="connected",
                telegram_chat_id="1", onboarding_completed_at=datetime.now(UTC))
    session.add(user)
    session.flush()
    session.add(UserConfig(user_id=user.id, config=CONFIG, schema_version=2))

    jobs = [CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test",
                      title=title, description="we build things", is_remote=True)
            for title in ("Backend Engineer", "Senior Backend Engineer", "Registered Nurse")]
    session.add_all(jobs)
    session.flush()

    app.dependency_overrides[deps.get_db] = lambda: session
    try:
        with TestClient(app, base_url="https://testserver") as http:
            http.cookies.set("access_token", issue_access_token(user.id, uuid.uuid4()), path="/")
            yield http, session, user, jobs
    finally:
        app.dependency_overrides.clear()
        session.close()
        trans.rollback()
        conn.close()
        os.environ.pop("DISABLE_RATE_LIMIT", None)


def _ids(body: dict) -> list[int]:
    return [job["id"] for job in body["jobs"]]


# ── the front page ────────────────────────────────────────────────────────────────────────────

def test_an_unfinished_signup_gets_the_onboarding_state_and_nothing_else(api):
    """Empty panels read as a broken product rather than an unfinished signup."""
    http, session, user, _ = api
    user.onboarding_completed_at = None
    session.flush()

    body = http.get("/dashboard").json()
    assert body["stage"] == "onboarding"
    assert body["onboarding"], "the onboarding state is passed through for the signup screens"
    assert "matching" not in body


def test_the_front_page_counts_what_the_jobs_page_lists(api):
    """One number in two places is one answer, or a person finds the two pages disagreeing."""
    from sqlalchemy import func

    from libs.db.models import UserJobState

    http, session, user, jobs = api
    session.add(UserJobState(user_id=user.id, job_id=jobs[0].id, applied_at=func.now()))
    session.flush()

    front = http.get("/dashboard").json()
    assert front["stage"] == "ready"
    assert front["matching"] == 2, "both backend roles match, the nurse does not"
    assert front["applied"] == 1

    # A dismissed job leaves both counts, or the front page promises a job the list does not have.
    session.add(UserJobState(user_id=user.id, job_id=jobs[1].id, dismissed_at=func.now()))
    session.flush()
    assert http.get("/dashboard").json()["matching"] == http.get("/feed").json()["total"] == 1


def test_a_broken_telegram_token_is_an_error_banner_not_a_trip_back_to_signup(api):
    """It stops delivery while everything else keeps working."""
    http, session, user, _ = api
    user.telegram_status = "broken"
    session.flush()

    banners = {banner["key"]: banner for banner in http.get("/dashboard").json()["banners"]}
    assert banners["telegram_broken"]["level"] == "error"


def test_a_paused_account_is_told_it_is_paused(api):
    """It used to be said only to accounts with a trained model, which after Phase 8 is nobody."""
    http, session, user, _ = api
    user.active = False
    session.flush()

    keys = {banner["key"] for banner in http.get("/dashboard").json()["banners"]}
    assert "paused" in keys


def test_the_dashboard_requires_signing_in(api):
    http, _, _, _ = api
    http.cookies.clear()
    assert http.get("/dashboard").status_code == 401


# ── the jobs page ─────────────────────────────────────────────────────────────────────────────

def test_green_words_pick_the_jobs_and_each_says_why(api):
    """The question a person has about every row: why am I seeing this?"""
    http, _, _, jobs = api
    body = http.get("/feed").json()

    assert set(_ids(body)) == {jobs[0].id, jobs[1].id}, "the nurse has none of the green words"
    for job in body["jobs"]:
        assert job["words"] == ["backend engineer"]
        assert job["score"] == 75, "a green word in the title"


def test_a_red_word_takes_a_job_off_the_page(api):
    from libs.db.models import UserConfig

    http, session, user, jobs = api
    session.get(UserConfig, user.id).config = {"sections": [{
        "platform": "all", "keywords": ["backend engineer"], "exclude_keywords": ["senior"]}]}
    session.flush()

    assert _ids(http.get("/feed").json()) == [jobs[0].id]


def test_a_dismissed_job_stays_off_the_page(api):
    """Dismissing has to mean something, or the button is a lie."""
    from sqlalchemy import func

    from libs.db.models import UserJobState

    http, session, user, jobs = api
    session.add(UserJobState(user_id=user.id, job_id=jobs[0].id, dismissed_at=func.now()))
    session.flush()

    body = http.get("/feed").json()
    assert _ids(body) == [jobs[1].id]
    assert body["total"] == 1


def test_the_feed_does_not_carry_the_full_description(api):
    """Up to 2,500 characters a job, and the list does not show it."""
    http, _, _, _ = api
    assert "description" not in http.get("/feed").json()["jobs"][0]


def test_the_feed_pages_and_reports_the_total_behind_the_page(api):
    """The pager needs the count under the same filters as the page, or "1 of 3" lies."""
    http, _, _, _ = api
    body = http.get("/feed?limit=1&offset=1").json()
    assert body["total"] == 2
    assert len(body["jobs"]) == 1


def test_the_search_box_reaches_titles_and_links(api):
    http, _, _, jobs = api
    assert _ids(http.get("/feed?q=senior").json()) == [jobs[1].id]
    # And the URL, because a pasted link is how people look a job back up.
    assert _ids(http.get(f"/feed?q={jobs[0].url.rsplit('/', 1)[1][:12]}").json()) == [jobs[0].id]


def test_the_applied_filter_splits_the_feed_both_ways(api):
    from sqlalchemy import func

    from libs.db.models import UserJobState

    http, session, user, jobs = api
    session.add(UserJobState(user_id=user.id, job_id=jobs[0].id, applied_at=func.now()))
    session.flush()

    assert _ids(http.get("/feed?applied=true").json()) == [jobs[0].id]
    assert _ids(http.get("/feed?applied=false").json()) == [jobs[1].id]
    # The applied state travels with the row either way, so the table can tag it.
    assert http.get("/feed?applied=true").json()["jobs"][0]["applied_at"] is not None


def test_remote_and_age_filters_narrow_the_matches(api):
    """Both are kept by matching when unknown, and both narrow only when asked to."""
    from libs.db.models import CorpusJob
    from libs.settings import SOURCE_MIN_AGE_HOURS

    http, session, _, jobs = api
    # A source whose own window is a week, so a three-day-old posting is still a match.
    assert SOURCE_MIN_AGE_HOURS["hiringcafe"] >= 96
    older = CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="hiringcafe",
                      title="Backend Engineer II", description="x", is_remote=None,
                      posted_at=datetime.now(UTC) - timedelta(days=3, hours=1))
    session.add(older)
    session.flush()

    assert older.id in _ids(http.get("/feed").json())
    assert older.id not in _ids(http.get("/feed?remote=true").json()), "unknown is not remote"
    assert older.id not in _ids(http.get("/feed?max_age_days=1").json())
    assert set(_ids(http.get("/feed?max_age_days=7").json())) == {older.id, jobs[0].id, jobs[1].id}


def test_a_salary_in_the_description_reaches_the_row_without_the_description(api):
    """The one thing the description holds that the table wants, extracted server-side for free."""
    http, session, _, jobs = api
    jobs[0].description = "Backend role paying €60,000 - €80,000 per year. We build things."
    session.flush()

    row = next(job for job in http.get("/feed").json()["jobs"] if job["id"] == jobs[0].id)
    assert row["salary"]["min"] == 60000
    assert row["salary"]["max"] == 80000
    assert row["salary"]["currency"] == "EUR"
    assert "description" not in row


def test_the_source_dropdown_offers_what_the_matches_came_from(api):
    """Not every board in the corpus: a source with nothing for this person is a dead option."""
    from libs.db.models import CorpusJob

    http, session, _, _ = api
    session.add_all([
        CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="greenhouse",
                  title="Backend Engineer III", description="x", is_remote=True),
        CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="nhs",
                  title="Staff Nurse", description="x", is_remote=True)])
    session.flush()

    assert http.get("/feed").json()["sources"] == ["greenhouse", "test"]
    assert [job["source"] for job in http.get("/feed?source=greenhouse").json()["jobs"]] == [
        "greenhouse"]


def test_equal_scores_are_newest_first_and_the_order_turns_around(api):
    """Freshness breaks a tie: the order `libs.matching` gives, not a second one made up here."""
    from libs.db.models import CorpusJob

    http, session, _, _ = api
    now = datetime.now(UTC)
    rows = {age: CorpusJob(url=f"https://example.test/tie-{age}", source="test",
                           title="Backend Engineer", description="x", is_remote=True,
                           first_seen_at=now - timedelta(hours=age))
            # The older one first, so its id is the lower: insertion order cannot pass for freshness.
            for age in (5, 1)}
    session.add_all(rows.values())
    session.flush()

    order = [i for i in _ids(http.get("/feed?sort=score").json()) if i in {r.id for r in rows.values()}]
    assert order == [rows[1].id, rows[5].id]
    turned = [i for i in _ids(http.get("/feed?sort=score&direction=asc").json())
              if i in {r.id for r in rows.values()}]
    assert turned == [rows[5].id, rows[1].id]


def test_a_missing_company_sorts_last_whichever_way_the_column_turns(api):
    http, session, _, jobs = api
    jobs[0].company, jobs[1].company = None, "Acme"
    session.flush()

    for direction in ("asc", "desc"):
        body = http.get(f"/feed?sort=company&direction={direction}").json()
        assert _ids(body) == [jobs[1].id, jobs[0].id], direction
