"""The dashboard and the feed.

Two properties carry the weight here, and both are about not lying to the user:

- **the feed says which stage ranked it.** Before a model exists it is keyword matching, and
  presenting that as "your model" would make the product look broken once the real model arrives
  and the ordering changes completely;
- **a stale model is named, not greyed out.** "Your profile changed after this model was trained"
  is a sentence somebody can act on.

The rest is aggregation, and aggregation is worth one test that the numbers come from where they
claim to.
"""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest

pytest.importorskip("fastapi")

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (no live Postgres)")

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
    from libs.db.models import CorpusJob, User, UserConfig, UserDocument, UserSecret
    from services.api import deps
    from services.api.app import app

    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")

    # A fully onboarded account: everything the seven pages write.
    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active",
                full_name="Alice Example", telegram_status="connected", telegram_chat_id="1",
                onboarding_completed_at=datetime.now(UTC),
                profile_generation=1, profile={"identity": {"name": "Alice"}, "confirmed": True})
    session.add(user)
    session.flush()
    for kind in ("cv", "history"):
        session.add(UserDocument(user_id=user.id, kind=kind, filename=f"{kind}.txt", text="x",
                                 text_sha256=uuid.uuid4().hex * 2, chars=1))
    for kind in ("claude_api", "telegram_bot", "proxy"):
        session.add(UserSecret(user_id=user.id, kind=kind, ciphertext=b"x", nonce=b"n"))
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


def _model(session, user, *, generation: int):
    from libs.db.models import UserModel

    model = UserModel(user_id=user.id, version=1, path="models/m.joblib",
                      feature_schema_version=1, profile_generation=generation,
                      embedder_model="e5", embedder_dim=384, embedder_version="v1",
                      n_labels=300, is_active=True)
    session.add(model)
    user.model_version = 1
    session.flush()
    return model


def test_an_unfinished_signup_gets_the_onboarding_state_and_nothing_else(api):
    """Empty panels read as a broken product rather than an unfinished signup."""
    http, session, user, _ = api
    user.onboarding_completed_at = None
    user.full_name = None
    session.flush()

    body = http.get("/dashboard").json()
    assert body["stage"] == "onboarding"
    assert "feed" not in body


def test_an_account_without_a_model_is_told_why_in_words(api):
    """A greyed-out button is not something a person can act on."""
    http, _, _, _ = api
    model = http.get("/dashboard").json()["model"]
    assert model["servable"] is False
    assert "no model yet" in model["stale_reason"]


def test_a_model_from_before_the_last_profile_change_is_stale(api):
    """The one-directional coupling made visible: a profile edit invalidates the artifact, and the
    user needs to know that is why their feed stopped improving."""
    http, session, user, _ = api
    _model(session, user, generation=1)
    assert http.get("/dashboard").json()["model"]["servable"] is True

    user.profile_generation = 2
    session.flush()

    model = http.get("/dashboard").json()["model"]
    assert model["servable"] is False
    assert "profile changed" in model["stale_reason"]


def test_a_broken_telegram_token_is_an_error_banner_not_a_trip_back_to_signup(api):
    """It stops delivery while everything else keeps working, so from the user's side it looks like
    "no jobs matched" rather than a broken connection.

    And it must **not** send a finished account back to the onboarding wizard: that would hide the
    dashboard which is the only place explaining what broke."""
    http, session, user, _ = api
    user.telegram_status = "broken"
    session.flush()

    banners = {banner["key"]: banner for banner in http.get("/dashboard").json()["banners"]}
    assert banners["telegram_broken"]["level"] == "error"


# ── the feed ──────────────────────────────────────────────────────────────────────────────────

def test_the_feed_says_when_it_is_only_keyword_matching(api):
    """Presenting stage 0 as "your model" would make the product look broken when the real model
    arrives and the ordering changes completely."""
    http, _, _, _ = api
    body = http.get("/feed").json()

    assert body["stage"] == "rules"
    assert body["approximate"] is True
    assert [job["title"] for job in body["jobs"]] == ["Backend Engineer",
                                                      "Senior Backend Engineer"]


def test_the_rules_feed_leaves_out_what_the_config_excludes(api):
    http, _, _, _ = api
    titles = [job["title"] for job in http.get("/feed").json()["jobs"]]
    assert "Registered Nurse" not in titles


def test_a_servable_model_takes_over_the_ranking(api):
    """And the stage changes with it, so the user can see why their feed suddenly reordered."""
    from libs.db.models import UserJobState

    http, session, user, jobs = api
    _model(session, user, generation=1)
    # Deliberately the reverse of the keyword ranking, so this cannot pass by coincidence.
    session.add(UserJobState(user_id=user.id, job_id=jobs[1].id, score=95.0))
    session.add(UserJobState(user_id=user.id, job_id=jobs[0].id, score=10.0))
    session.flush()

    body = http.get("/feed").json()
    assert body["stage"] == "personal"
    assert [job["title"] for job in body["jobs"]] == ["Senior Backend Engineer", "Backend Engineer"]


def test_a_dismissed_job_stays_out_of_both_feeds(api):
    """Dismissing has to mean something, or the button is a lie."""
    from sqlalchemy import func

    from libs.db.models import UserJobState

    http, session, user, jobs = api
    session.add(UserJobState(user_id=user.id, job_id=jobs[0].id, dismissed_at=func.now()))
    session.flush()

    assert "Backend Engineer" not in [job["title"] for job in http.get("/feed").json()["jobs"]][:1]


def test_the_feed_does_not_carry_the_full_description(api):
    """Up to 2,500 characters a job, and the list does not show it — 25 of them would be most of the
    response for none of the value."""
    http, _, _, _ = api
    assert "description" not in http.get("/feed").json()["jobs"][0]


def test_the_feed_pages_and_reports_the_total_behind_the_page(api):
    """The pager needs the count under the same filters as the page, or "1 of 3" lies."""
    from libs.db.models import UserJobState

    http, session, user, jobs = api
    _model(session, user, generation=1)
    for index, job in enumerate(jobs):
        session.add(UserJobState(user_id=user.id, job_id=job.id, score=90.0 - index))
    session.flush()

    body = http.get("/feed?limit=2&offset=2").json()
    assert body["total"] == 3
    assert len(body["jobs"]) == 1


def test_the_search_box_reaches_titles_and_links(api):
    from libs.db.models import UserJobState

    http, session, user, jobs = api
    _model(session, user, generation=1)
    for job in jobs:
        session.add(UserJobState(user_id=user.id, job_id=job.id, score=50.0))
    session.flush()

    titles = [job["title"] for job in http.get("/feed?q=nurse").json()["jobs"]]
    assert titles == ["Registered Nurse"]
    # And the URL, because a pasted link is how people look a job back up.
    by_url = http.get(f"/feed?q={jobs[0].url.rsplit('/', 1)[1][:12]}").json()["jobs"]
    assert [job["id"] for job in by_url] == [jobs[0].id]


def test_the_applied_filter_splits_the_feed_both_ways(api):
    from sqlalchemy import func as sqlfunc

    from libs.db.models import UserJobState

    http, session, user, jobs = api
    _model(session, user, generation=1)
    session.add(UserJobState(user_id=user.id, job_id=jobs[0].id, score=50.0,
                             applied_at=sqlfunc.now()))
    session.add(UserJobState(user_id=user.id, job_id=jobs[1].id, score=60.0))
    session.flush()

    assert [job["id"] for job in http.get("/feed?applied=true").json()["jobs"]] == [jobs[0].id]
    assert [job["id"] for job in http.get("/feed?applied=false").json()["jobs"]] == [jobs[1].id]
    # The applied state travels with the row either way, so the table can tag it.
    assert http.get("/feed?applied=true").json()["jobs"][0]["applied_at"] is not None


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


def test_the_source_dropdown_lists_what_the_corpus_actually_has(api):
    http, _, _, _ = api
    assert http.get("/feed").json()["sources"] == ["test"]


def test_the_rules_feed_takes_the_same_filters(api):
    """Stage 0 filters in Python rather than SQL, and must not behave differently for it."""
    http, _, _, _ = api
    body = http.get("/feed?q=senior&limit=1").json()
    assert body["total"] == 1
    assert [job["title"] for job in body["jobs"]] == ["Senior Backend Engineer"]

    assert http.get("/feed?source=nowhere").json()["jobs"] == []


def test_the_dashboard_requires_signing_in(api):
    http, _, _, _ = api
    http.cookies.clear()
    assert http.get("/dashboard").status_code == 401
